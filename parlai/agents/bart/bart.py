#!/usr/bin/env python3

# Copyright (c) Facebook, Inc. and its affiliates.
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.
"""
BART: Denoising Sequence-to-Sequence Pre-training for
Natural Language Generation, Translation, and Comprehension

See https://arxiv.org/abs/1910.13461.

The BART agent can be instantiated as simply `-m bart`,
however it is recommended to specify `--init-model zoo:bart/bart_large/model`
or `-mf zoo:bart/bart_large/model` to ensure correct dictionaries are saved.
"""
import os
import torch
from typing import Optional

from parlai.agents.bart.convert_fairseq_to_parlai import ConversionScript
from parlai.agents.bart.modules import BartModel
from parlai.agents.transformer.transformer import TransformerGeneratorAgent
from parlai.core.agents import compare_init_model_opts
from parlai.core.message import Message
from parlai.core.opt import Opt
from parlai.core.params import ParlaiParser
from parlai.core.torch_agent import Batch, History
from parlai.utils.typing import TShared
from parlai.zoo.bart.build import download, CONVERSION_ARGS, BART_ARGS


class BartAgent(TransformerGeneratorAgent):
    """
    BART Agent.

    Relies on the BART model implemented in fairseq.

    If you have a fine-tuned BART model from fairseq, you can specify the
    `--init-fairseq-model` arg, which will convert your fine-tuned model
    to a ParlAI model.
    """

    @staticmethod
    def add_cmdline_args(argparser: ParlaiParser):
        """
        Override to add init-fairseq-model arg.
        """
        TransformerGeneratorAgent.add_cmdline_args(argparser)
        group = argparser.add_argument_group('Bart Args')
        group.add_argument(
            '--init-fairseq-model',
            type=str,
            default=None,
            help='fairseq checkpoint for bart',
        )
        argparser.set_defaults(dict_tokenizer='gpt2')

    def __init__(self, opt: Opt, shared: TShared = None):
        if not shared:
            opt = self._initialize_bart(opt)
        super().__init__(opt, shared)

    def _initialize_bart(self, opt: Opt) -> Opt:
        """
        Download and convert BART pre-trained models.

        Additionally, convert `init-fairseq-model` if necessary.

        :param opt:
            ParlAI-parsed options

        :return opt:
            return opt with BART-specific args.
        """
        if not opt.get('converting'):
            download(opt['datapath'])
            opt['init_model'] = os.path.join(
                opt['datapath'], 'models/bart/bart_large/model'
            )
        if opt.get('init_fairseq_model'):
            opt = self._convert_model(opt)
        opt.update(BART_ARGS)
        compare_init_model_opts(opt, opt)
        return opt

    def _convert_model(self, opt: Opt) -> Opt:
        """
        Convert fairseq init model to ParlAI Model.

        :param opt:
            options

        :return opt:
            return opt with new init_model path
        """
        model_name = os.path.split(opt['init_fairseq_model'])[-1]
        args = CONVERSION_ARGS.copy()

        args['input'] = [opt['init_fairseq_model']]
        if opt.get('model_file') and not os.path.exists(opt['model_file']):
            args['output'] = opt['model_file']
        else:
            args['output'] = os.path.join(
                opt['datapath'], 'models/converted_fairseq_models/', model_name
            )

        ConversionScript.main(**args)
        opt['init_model'] = args['output']
        return opt

    def build_model(self) -> BartModel:
        """
        Build and return model.
        """
        model = BartModel(self.opt, self.dict)
        if self.opt['embedding_type'] != 'random':
            self._copy_embeddings(
                model.encoder.embeddings.weight, self.opt['embedding_type']
            )
        return model

    def _set_text_vec(
        self, obs: Message, history: History, truncate: Optional[int]
    ) -> Message:
        """
        Override to prepend start token and append end token.
        """
        obs = super()._set_text_vec(obs, history, truncate)
        if 'text' not in obs or 'text_vec' not in obs:
            return obs
        vec = obs['text_vec']
        if truncate is not None:
            vec = torch.LongTensor(  # type: ignore
                self._check_truncate(obs['text_vec'], truncate - 2, True)
            )
        obs.force_set(
            'text_vec', self._add_start_end_tokens(vec, add_start=True, add_end=True)
        )
        return obs

    def _get_initial_decoder_input(self, bsz: int, beam_size: int, dev: torch.device):
        """
        Override to seed decoder with EOS token.

        See docstring for `BartAgent._generate` for more details.
        """
        return (
            torch.LongTensor(  # type: ignore
                [self.END_IDX]
            )
            .expand(bsz * beam_size, 1)
            .to(dev)
        )

    def _generate(
        self,
        batch: Batch,
        beam_size: int,
        max_ts: int,
        prefix_tokens: Optional[torch.LongTensor] = None,
    ):
        """
        Override to set prefix_tokens.

        For bart pretraining, a bos token was added to the input.

        input to encoder:
        <bos> seq <eos>

        input to decoder:
        <eos> <bos> seq

        target is:
        <bos> seq <eos>
        """
        text_vec = batch.text_vec  # type: ignore
        if text_vec is not None:
            prefix_tokens = text_vec.new_zeros(  # type: ignore
                (text_vec.size(0), 1)
            ).fill_(self.START_IDX)
        return super()._generate(batch, beam_size, max_ts, prefix_tokens)
