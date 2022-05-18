# Copyright (c) OpenMMLab. All rights reserved.
from collections import OrderedDict

import mmcv.fileio
import torch
import torch.distributed as dist
from mmcv.runner import BaseModule

from mmrazor.registry import MODELS


@MODELS.register_module()
class BaseAlgorithm(BaseModule):
    """Base class for algorithms, it consists of two main parts: architecture
    and algorithm components.

    Args:
        architecture (dict): Config for architecture to be slimmed.
        mutator (dict): Config for mutator, which is an algorithm component
            for NAS.
        pruner (dict): Config for pruner, which is an algorithm component
            for pruning.
        distiller (dict): Config for pruner, which is an algorithm component
            for knowledge distillation.
        retraining (bool): Whether is in retraining stage, if False,
            it is in pre-training stage.
        init_cfg (dict): Init config for ``BaseModule``.
        mutable_cfg (dict): Config for mutable of the subnet searched out,
            it will be needed in retraining stage.
        channel_cfg (dict): Config for channel of the subnet searched out,
            it will be needed in retraining stage.
    """

    def __init__(
        self,
        architecture,
        mutator=None,
        pruner=None,
        distiller=None,
        retraining=False,
        init_cfg=None,
        mutable_cfg=None,
        channel_cfg=None,
    ):
        super(BaseAlgorithm, self).__init__(init_cfg)
        self.retraining = retraining
        if self.retraining:
            self.mutable_cfg = self.load_subnet(mutable_cfg)
            self.channel_cfg = self.load_subnet(channel_cfg)
        self.architecture = MODELS.build(architecture)

        self.deployed = False
        self._init_mutator(mutator)
        self._init_pruner(pruner)
        self._init_distiller(distiller)

    def load_subnet(self, subnet_path):
        """Load subnet searched out in search stage.

        Args:
            subnet_path (str | list[str] | tuple(str)): The path of saved
                subnet file, its suffix should be .yaml.
            There may be several subnet searched out in some algorithms.

        Returns:
            dict | list[dict]: Config(s) for subnet(s) searched out.
        """
        if subnet_path is None:
            return

        if isinstance(subnet_path, str):
            cfg = mmcv.fileio.load(subnet_path)
        elif isinstance(subnet_path, (list, tuple)):
            cfg = list()
            for path in subnet_path:
                cfg.append(mmcv.fileio.load(path))
        else:
            raise NotImplementedError

        return cfg

    def _init_mutator(self, mutator):
        """Build registered mutators and make preparations.

        Args:
            mutator (dict): Config for mutator, which is an algorithm component
                for NAS.
        """
        if mutator is None:
            self.mutator = None
            return
        self.mutator = MODELS.build(mutator)
        self.mutator.prepare_from_supernet(self.architecture)
        if self.retraining:
            if isinstance(self.mutable_cfg, dict):
                self.mutator.deploy_subnet(self.architecture, self.mutable_cfg)
                self.deployed = True
            else:
                raise NotImplementedError

    def _init_pruner(self, pruner):
        """Build registered pruners and make preparations.

        Args:
            pruner (dict): Config for pruner, which is an algorithm component
                for pruning.
        """
        if pruner is None:
            self.pruner = None
            return
        self.pruner = MODELS.build(pruner)

        if self.retraining:
            if isinstance(self.channel_cfg, dict):
                self.pruner.deploy_subnet(self.architecture, self.channel_cfg)
                self.deployed = True
            else:
                raise NotImplementedError
        else:
            self.pruner.prepare_from_supernet(self.architecture)

    def _init_distiller(self, distiller):
        """Build registered distillers and make preparations.

        Args:
            distiller (dict): Config for pruner, which is an algorithm
                component for knowledge distillation.
        """
        if distiller is None:
            self.distiller = None
            return
        self.distiller = MODELS.build(distiller)
        self.distiller.prepare_from_student(self.architecture)

    @property
    def with_mutator(self):
        """Whether or not this property exists."""
        return hasattr(self, 'mutator') and self.mutator is not None

    @property
    def with_pruner(self):
        """Whether or not this property exists."""
        return hasattr(self, 'pruner') and self.pruner is not None

    @property
    def with_distiller(self):
        """Whether or not this property exists."""
        return hasattr(self, 'distiller') and self.distiller is not None

    def forward(self, *args, return_loss=True, **kwargs):
        """Calls either forward_train or forward_test depending on whether
        return_loss=True.

        Note this setting will change the expected inputs. When
        ``return_loss=True``, img and img_meta are single-nested (i.e. Tensor
        and List[dict]), and when ``resturn_loss=False``, img and img_meta
        should be double nested (i.e.  List[Tensor], List[List[dict]]), with
        the outer list indicating test time augmentations.
        """
        if return_loss:
            return self.forward_train(*args, **kwargs)
        else:
            return self.forward_test(*args, return_loss=False, **kwargs)

    def forward_train(self, *args, **kwargs):
        return self.architecture(*args, return_loss=True, **kwargs)

    def forward_test(self, *args, **kwargs):
        return self.architecture(*args, return_loss=False, **kwargs)

    def show_result(self, *args, **kwargs):
        """Draw `result` over `img`"""
        return self.architecture.show_result(*args, **kwargs)

    # TODO maybe remove
    def _parse_losses(self, losses):
        """Parse the raw outputs (losses) of the network.

        Args:
            losses (dict): Raw output of the network, which usually contain
                losses and other necessary information.
        Returns:
            tuple[Tensor, dict]: (loss, log_vars), loss is the loss tensor
                which may be a weighted sum of all losses, log_vars contains
                all the variables to be sent to the logger.
        """
        log_vars = OrderedDict()
        for loss_name, loss_value in losses.items():
            if isinstance(loss_value, torch.Tensor):
                log_vars[loss_name] = loss_value.mean()
            elif isinstance(loss_value, list):
                log_vars[loss_name] = sum(_loss.mean() for _loss in loss_value)
            elif isinstance(loss_value, dict):
                for name, value in loss_value.items():
                    log_vars[name] = value
            else:
                raise TypeError(
                    f'{loss_name} is not a tensor or list of tensors')

        loss = sum(_value for _key, _value in log_vars.items()
                   if 'loss' in _key)

        log_vars['loss'] = loss
        for loss_name, loss_value in log_vars.items():
            # reduce loss when distributed training
            if dist.is_available() and dist.is_initialized():
                loss_value = loss_value.data.clone()
                dist.all_reduce(loss_value.div_(dist.get_world_size()))
            log_vars[loss_name] = loss_value.item()

        return loss, log_vars
