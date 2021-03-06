# Copyright (c) 2020 PaddlePaddle Authors. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from flask import Flask, request, abort
from paddle_serving_server_gpu import OpMaker, OpSeqMaker, Server
import paddle_serving_server_gpu as serving
from multiprocessing import Pool, Process, Queue
from paddle_serving_client import Client
from paddle_serving_server_gpu.serve import start_multi_card

import sys
import numpy as np


class WebService(object):
    def __init__(self, name="default_service"):
        self.name = name
        self.gpus = []
        self.rpc_service_list = []

    def load_model_config(self, model_config):
        self.model_config = model_config

    def set_gpus(self, gpus):
        self.gpus = [int(x) for x in gpus.split(",")]

    def default_rpc_service(self,
                            workdir="conf",
                            port=9292,
                            gpuid=0,
                            thread_num=10):
        device = "gpu"
        if gpuid == -1:
            device = "cpu"
        op_maker = serving.OpMaker()
        read_op = op_maker.create('general_reader')
        general_infer_op = op_maker.create('general_infer')
        general_response_op = op_maker.create('general_response')

        op_seq_maker = serving.OpSeqMaker()
        op_seq_maker.add_op(read_op)
        op_seq_maker.add_op(general_infer_op)
        op_seq_maker.add_op(general_response_op)

        server = serving.Server()
        server.set_op_sequence(op_seq_maker.get_op_sequence())
        server.set_num_threads(thread_num)

        server.load_model_config(self.model_config)
        if gpuid >= 0:
            server.set_gpuid(gpuid)
        server.prepare_server(workdir=workdir, port=port, device=device)
        return server

    def _launch_rpc_service(self, service_idx):
        self.rpc_service_list[service_idx].run_server()

    def prepare_server(self, workdir="", port=9393, device="gpu", gpuid=0):
        self.workdir = workdir
        self.port = port
        self.device = device
        self.gpuid = gpuid
        if len(self.gpus) == 0:
            # init cpu service
            self.rpc_service_list.append(
                self.default_rpc_service(
                    self.workdir, self.port + 1, -1, thread_num=10))
        else:
            for i, gpuid in enumerate(self.gpus):
                self.rpc_service_list.append(
                    self.default_rpc_service(
                        "{}_{}".format(self.workdir, i),
                        self.port + 1 + i,
                        gpuid,
                        thread_num=10))

    def _launch_web_service(self):
        gpu_num = len(self.gpus)
        self.client = Client()
        self.client.load_client_config("{}/serving_server_conf.prototxt".format(
            self.model_config))
        endpoints = ""
        if gpu_num > 0:
            for i in range(gpu_num):
                endpoints += "127.0.0.1:{},".format(self.port + i + 1)
        else:
            endpoints = "127.0.0.1:{}".format(self.port + 1)
        self.client.connect([endpoints])

    def get_prediction(self, request):
        if not request.json:
            abort(400)
        if "fetch" not in request.json:
            abort(400)
        feed, fetch = self.preprocess(request.json, request.json["fetch"])
        fetch_map_batch = self.client.predict(feed=feed, fetch=fetch)
        fetch_map_batch = self.postprocess(
            feed=request.json, fetch=fetch, fetch_map=fetch_map_batch)
        for key in fetch_map_batch:
            fetch_map_batch[key] = fetch_map_batch[key].tolist()
        result = {"result": fetch_map_batch}
        return result

    def run_server(self):
        import socket
        localIP = socket.gethostbyname(socket.gethostname())
        print("web service address:")
        print("http://{}:{}/{}/prediction".format(localIP, self.port,
                                                  self.name))
        server_pros = []
        for i, service in enumerate(self.rpc_service_list):
            p = Process(target=self._launch_rpc_service, args=(i, ))
            server_pros.append(p)
        for p in server_pros:
            p.start()

    def preprocess(self, feed={}, fetch=[]):
        return feed, fetch

    def postprocess(self, feed={}, fetch=[], fetch_map=None):
        return fetch_map
