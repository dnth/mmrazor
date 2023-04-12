# Copyright (c) OpenMMLab. All rights reserved.
import unittest

import torch
import torch.nn as nn

from mmrazor.implementations.pruning import sparse_gpt


class TestSparseGptOps(unittest.TestCase):

    @torch.no_grad()
    def test_op(self):

        def get_loss(linear, linear1, data):
            y = linear(data)
            y1 = linear1(data)
            return (y - y1).square().sum()

        def infer(model, dataset):
            for x in dataset:
                model(x)

        for device in ['cpu', 'cuda']:
            device = torch.device(device)

            # prepare

            linear = nn.Linear(12, 20, bias=False).to(device)
            sparse_linear = sparse_gpt.SparseGptLinear(
                12, 20, bias=False).to(device)
            sparse_linear.load_state_dict(linear.state_dict(), strict=False)

            random_data = torch.rand([100, 5, 12]).to(
                device)  # [loader_batch,batch,feature]
            data_0 = random_data[0]

            self.assertTrue(get_loss(linear, sparse_linear, data_0) == 0)

            # prune

            sparse_linear.start_init_hessian()
            infer(sparse_linear, random_data)
            sparse_linear.end_init_hessian()

            sparse_linear.prune_24()

            # compare

            print('norm:', linear(data_0).norm(2))
            print('distance:', get_loss(linear, sparse_linear, data_0))