import random
from typing import Any, Dict, List, Optional

import torch
from torch.nn.utils.rnn import pad_sequence
from transformers import DataCollatorForLanguageModeling, DataCollatorForWholeWordMask
from transformers.data.data_collator import _torch_collate_batch
from transformers.models.bart.modeling_bart import shift_tokens_right
from transformers.tokenization_utils_base import PreTrainedTokenizerBase


def tolist(x):
    if isinstance(x, list):
        return x
    elif hasattr(x, "numpy"):  # Checks for TF tensors without needing the import
        x = x.numpy()
    return x.tolist()


def pad_for_token_type_ids(examples: Any, tokenizer: PreTrainedTokenizerBase, pad_to_multiple_of=8) -> torch.Tensor:
    """
    Create "token_type_ids" for Bert-like models
    used when token_a & token_b already in the same chunk separated by [SEP] token
    (for those not in the same chunk, use "tokenizer.create_token_type_ids_from_sequences(token_a, token_b)")
    """
    if isinstance(examples, torch.Tensor):
        examples = examples.tolist()
    if max([len(example) for example in examples]) % pad_to_multiple_of == 0:
        max_seq_len = max([len(example) for example in examples])
    else:
        max_seq_len = (
            pad_to_multiple_of
            + (max([len(example) for example in examples]) // pad_to_multiple_of) * pad_to_multiple_of
        )
    token_type_ids_with_padding = []
    for example in examples:
        for idx in range(len(example)):
            if example[idx] == tokenizer.sep_token_id and idx != len(example) - 1:
                token_type_ids_with_padding.append([0] * (idx + 1) + [1] * (max_seq_len - idx - 1))
                break
            if idx == len(example) - 1:
                token_type_ids_with_padding.append([0 for _ in range(max_seq_len)])
    return torch.tensor(token_type_ids_with_padding).long()


class DataCollatorForBert(DataCollatorForWholeWordMask):
    """
    Processing training examples to mini-batch for Bert (mlm+wwm+sop).
    """

    def __init__(
        self,
        tokenizer: PreTrainedTokenizerBase,
        mlm_probability: float = 0.15,
        pad_to_multiple_of: Optional[int] = None,
    ):
        self.tokenizer = tokenizer
        self.mlm_probability = mlm_probability
        self.pad_to_multiple_of = pad_to_multiple_of

    def __call__(self, examples: List[Dict[str, Any]]) -> Dict[str, Any]:
        examples = self._prepare_wwm_and_sop_from_examples(examples)
        batch = self.tokenizer.pad(examples, return_tensors="pt", pad_to_multiple_of=self.pad_to_multiple_of)
        batch_mask = batch.pop("mask_label")
        batch["input_ids"], batch["labels"] = self.torch_mask_tokens(batch["input_ids"], batch_mask)
        return batch

    def _prepare_wwm_and_sop_from_examples(self, examples: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        output_examples = []
        for example in examples:
            chunk_ids = example["input_ids"]
            seq_length = len(chunk_ids)
            start, end = seq_length // 3, seq_length // 3 * 2
            split_position = random.randrange(start, end)
            reverse = random.random() < 0.5

            if reverse:
                token_a = chunk_ids[split_position:]
                token_b = chunk_ids[:split_position]
            else:
                token_a = chunk_ids[:split_position]
                token_b = chunk_ids[split_position:]

            input_ids = self.tokenizer.build_inputs_with_special_tokens(token_a, token_b)
            token_type_ids = self.tokenizer.create_token_type_ids_from_sequences(token_a, token_b)
            sentence_order_label = 1 if reverse else 0
            ref_tokens = self.tokenizer.convert_ids_to_tokens(input_ids)
            mask_label = self._whole_word_mask(ref_tokens)

            output_examples.append(
                {
                    "input_ids": input_ids,
                    "token_type_ids": token_type_ids,
                    "next_sentence_label": sentence_order_label,
                    "mask_label": mask_label,
                }
            )
        return output_examples


class DataCollatorForAlbert(DataCollatorForLanguageModeling):
    """
    Processing training examples to mini-batch for Albert (mlm+sop).
    """

    def __init__(
        self,
        tokenizer: PreTrainedTokenizerBase,
        mlm_probability: float = 0.15,
        pad_to_multiple_of: Optional[int] = None,
    ):
        self.tokenizer = tokenizer
        self.mlm_probability = mlm_probability
        self.pad_to_multiple_of = pad_to_multiple_of

    def __call__(self, examples: List[Dict[str, Any]]) -> Dict[str, Any]:
        examples = self._prepare_sop_from_examples(examples)
        batch = self.tokenizer.pad(examples, return_tensors="pt", pad_to_multiple_of=self.pad_to_multiple_of)

        special_tokens_mask = batch.pop("special_tokens_mask", None)
        batch["input_ids"], batch["labels"] = self.torch_mask_tokens(
            batch["input_ids"], special_tokens_mask=special_tokens_mask
        )
        return batch

    def _prepare_sop_from_examples(self, examples: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        output_examples = []
        for example in examples:
            chunk_ids = example["input_ids"]
            seq_length = len(chunk_ids)
            start, end = seq_length // 3, seq_length // 3 * 2
            split_position = random.randrange(start, end)
            reverse = random.random() < 0.5

            if reverse:
                token_a = chunk_ids[split_position:]
                token_b = chunk_ids[:split_position]
            else:
                token_a = chunk_ids[:split_position]
                token_b = chunk_ids[split_position:]

            input_ids = self.tokenizer.build_inputs_with_special_tokens(token_a, token_b)
            token_type_ids = self.tokenizer.create_token_type_ids_from_sequences(token_a, token_b)
            special_tokens_mask = self.tokenizer.get_special_tokens_mask(input_ids, already_has_special_tokens=True)
            sentence_order_label = 1 if reverse else 0

            output_examples.append(
                {
                    "input_ids": input_ids,
                    "token_type_ids": token_type_ids,
                    "special_tokens_mask": special_tokens_mask,
                    "sentence_order_label": sentence_order_label,
                }
            )
        return output_examples


class DataCollatorForRoberta(DataCollatorForLanguageModeling):
    """
    Processing training examples to mini-batch for Roberta (mlm).
    """

    def __init__(
        self,
        tokenizer: PreTrainedTokenizerBase,
        mlm_probability: float = 0.15,
        pad_to_multiple_of: Optional[int] = None,
    ) -> None:
        super().__init__(
            tokenizer=tokenizer, mlm=True, mlm_probability=mlm_probability, pad_to_multiple_of=pad_to_multiple_of
        )


class DataCollatorForGpt2:
    """
    Processing training examples to mini-batch for Gpt2 (clm).
    """

    def __init__(
        self,
        tokenizer: PreTrainedTokenizerBase,
        pad_to_multiple_of: Optional[int] = None,
    ):
        self.tokenizer = tokenizer
        self.pad_to_multiple_of = pad_to_multiple_of

    def __call__(self, examples):
        examples = [example["input_ids"] for example in examples]
        batch = {
            "input_ids": _torch_collate_batch(
                examples, tokenizer=self.tokenizer, pad_to_multiple_of=self.pad_to_multiple_of
            )
        }
        batch["labels"] = batch["input_ids"].clone()
        return batch


# Ref: https://github.com/cosmoquester/transformers-bart-pretrain/blob/master/transformers_bart_pretrain/data.py
class DataCollatorForBart:
    """
    Processing training examples to mini-batch for Bart (text-infilling)
    """

    def __init__(
        self,
        tokenizer: PreTrainedTokenizerBase,
        mlm_probability=0.15,
        poisson_lambda=3,
        pad_to_multiple_of: Optional[int] = None,
    ):
        self.tokenizer = tokenizer
        self.mlm_probability = mlm_probability
        self.poisson_dist = torch.distributions.Poisson(poisson_lambda)
        self.pad_to_multiple_of = pad_to_multiple_of

    def __call__(self, examples):
        examples = [example["input_ids"] for example in examples]
        batch = {"labels": _torch_collate_batch(examples, tokenizer=self.tokenizer, pad_to_multiple_of=None)}
        batch["labels"] = torch.where(batch["labels"] == self.tokenizer.pad_token_id, -100, batch["labels"])
        batch["decoder_input_ids"] = shift_tokens_right(
            batch["labels"], self.tokenizer.pad_token_id, self.tokenizer.eos_token_id
        )
        batch["decoder_attention_mask"] = torch.where(
            batch["decoder_input_ids"] == self.tokenizer.pad_token_id, 0, torch.ones_like(batch["decoder_input_ids"])
        )
        batch["input_ids"] = self._infilling(examples)
        batch["attention_mask"] = torch.where(
            batch["input_ids"] == self.tokenizer.pad_token_id, 0, torch.ones_like(batch["input_ids"])
        )
        return batch

    def _infilling(self, examples):
        buffer = []
        for example in examples:
            source_tokens_ids = example
            source_tokens_ids_length = len(example)
            masking_length = int(source_tokens_ids_length * self.mlm_probability)
            masked_length = 0

            while masked_length < masking_length:
                span_length = int(min(self.poisson_dist.sample().item(), source_tokens_ids_length - 1))
                start_index = torch.randint(0, source_tokens_ids_length - span_length, (1,)).item()
                source_tokens_ids = (
                    source_tokens_ids[:start_index]
                    + [self.tokenizer.mask_token_id]
                    + source_tokens_ids[start_index + span_length :]
                )
                source_tokens_ids_length -= span_length - 1
                masked_length += span_length
            buffer.append(source_tokens_ids)
        return _torch_collate_batch(buffer, tokenizer=self.tokenizer, pad_to_multiple_of=self.pad_to_multiple_of)


class DataCollatorForT5:
    """
    Processing training examples to mini-batch for T5
    """

    def __init__(
        self,
        tokenizer: PreTrainedTokenizerBase,
        pad_to_multiple_of: int = 8,
        noise_density: float = 0.15,
        mean_span_length: float = 3.0,
    ):
        self.tokenizer = tokenizer
        self.pad_to_multiple_of = pad_to_multiple_of
        self.noise_density = noise_density
        self.mean_span_length = mean_span_length

    def _random_spans_noise_mask(self, length: int) -> torch.BoolTensor:
        """pytorch-ported version of https://github.com/google-research/text-to-text-transfer-transformer/blob/bb545f19ec221e6203dd05505573fbc0c0a9001f/t5/data/preprocessors.py#L2901"""
        orig_len = length
        length = max(length, 2)  # set minumum to 2 to avoid degeneracy
        num_noise_tokens = round(self.noise_density * length)
        num_noise_tokens = min(max(num_noise_tokens, 1), length - 1)  # set maximum to length-1
        num_noise_spans = round(num_noise_tokens / self.mean_span_length)
        num_noise_spans = max(num_noise_spans, 1)  # set minumum to 1
        num_nonnoise_tokens = length - num_noise_tokens

        def _random_segmentation(num_items, num_segments):
            # affected by global seed
            bars = torch.arange(num_items - 1) < num_segments - 1
            bars = bars[torch.randperm(bars.size(0))]
            bars = torch.cat((torch.tensor([0]), bars), dim=0)  # to make segment 0 nonzero
            segment_id = torch.cumsum(bars, dim=0)
            segment_length = torch.zeros(num_segments, dtype=torch.long).scatter_add(
                0, segment_id, torch.ones_like(segment_id)
            )
            return segment_length

        noise_span_lengths = _random_segmentation(num_noise_tokens, num_noise_spans)
        nonnoise_span_lengths = _random_segmentation(num_nonnoise_tokens, num_noise_spans)
        interleaved_span_lengths = torch.stack((nonnoise_span_lengths, noise_span_lengths), dim=1).reshape(-1)
        span_starts = torch.cumsum(interleaved_span_lengths, dim=0)[:-1]
        span_start_indicator = torch.zeros(length).long().scatter(0, span_starts, torch.ones_like(span_starts))
        span_num = torch.cumsum(span_start_indicator, dim=0)
        is_noise = span_num % 2 == 1
        return is_noise[:orig_len]

    def _noise_span_to_unique_sentinel(self, tokens, noise_mask, append_last_sentinel=False) -> torch.LongTensor:
        """pytorch-ported version of https://github.com/google-research/text-to-text-transfer-transformer/blob/bb545f19ec221e6203dd05505573fbc0c0a9001f/t5/data/preprocessors.py#L3074"""
        if not isinstance(tokens, torch.Tensor):
            tokens = torch.tensor(tokens)
        prev_token_is_noise = torch.cat((torch.tensor([0]), noise_mask[:-1]), dim=0).bool()
        first_noise_tokens = torch.logical_and(noise_mask, torch.logical_not(prev_token_is_noise))
        subsequent_noise_tokens = torch.logical_and(noise_mask, prev_token_is_noise)
        sentinel = self.tokenizer.get_vocab()["<extra_id_0>"] + 1 - torch.cumsum(first_noise_tokens.long(), dim=0)
        tokens = torch.where(first_noise_tokens, sentinel, tokens)
        ret = torch.masked_select(tokens, torch.logical_not(subsequent_noise_tokens))
        if append_last_sentinel:  # target masking needs additional sentinel token at last position
            last_sentinel_id = sentinel.min().reshape(-1) - 1
            ret = torch.cat((ret, last_sentinel_id), dim=0)
        ret = torch.cat((ret, torch.tensor([self.tokenizer.eos_token_id], dtype=torch.long)), dim=0)  # add eos token
        return ret

    def __call__(self, examples):
        examples = [example["input_ids"] for example in examples]
        example_n = len(examples)
        example_len = len(examples[0])
        noise_masks = [self._random_spans_noise_mask(example_len) for _ in range(example_n)]
        inputs = [
            self._noise_span_to_unique_sentinel(example, noise_mask)
            for example, noise_mask in zip(examples, noise_masks)
        ]
        targets = [
            self._noise_span_to_unique_sentinel(example, ~noise_mask, append_last_sentinel=True)
            for example, noise_mask in zip(examples, noise_masks)
        ]
        # make labels and input_ids
        batch = {
            "input_ids": _torch_collate_batch(
                inputs,
                tokenizer=self.tokenizer,
                pad_to_multiple_of=None,  # all samples' length are set to self.max_length by design
            ),
            "labels": _torch_collate_batch(
                targets, tokenizer=self.tokenizer, pad_to_multiple_of=None  # labels' length are all sample by design
            ),
        }
        batch["decoder_input_ids"] = shift_tokens_right(
            batch["labels"], self.tokenizer.pad_token_id, self.tokenizer.pad_token_id
        )
        batch["decoder_attention_mask"] = torch.where(
            batch["decoder_input_ids"] == self.tokenizer.pad_token_id, 0, torch.ones_like(batch["decoder_input_ids"])
        )
        batch["attention_mask"] = torch.where(
            batch["input_ids"] == self.tokenizer.pad_token_id, 0, torch.ones_like(batch["input_ids"])
        )
        return batch


class DataCollatorForElectra(DataCollatorForWholeWordMask):
    """
    Processing training examples to mini-batch for Electra (fake input discrimination).
    """

    def __init__(self, tokenizer: PreTrainedTokenizerBase, mlm_probability: float = 0.15, pad_to_multiple_of: int = 8):
        self.tokenizer = tokenizer
        self.mlm_probability = mlm_probability
        self.pad_to_multiple_of = pad_to_multiple_of

    def __call__(self, examples: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        examples = [
            example["input_ids"].tolist() if isinstance(example["input_ids"], torch.Tensor) else example["input_ids"]
            for example in examples
        ]
        fake_inputs_with_labels = self._generate_fake_inputs(examples)
        batch = {
            "input_ids": _torch_collate_batch(
                fake_inputs_with_labels["input_ids"], self.tokenizer, pad_to_multiple_of=self.pad_to_multiple_of
            ),
            "labels": _torch_collate_batch(
                fake_inputs_with_labels["labels"], self.tokenizer, pad_to_multiple_of=self.pad_to_multiple_of
            ),
        }
        batch["attention_mask"] = (
            torch.ones(batch["input_ids"].size()) - (batch["input_ids"] == self.tokenizer.pad_token_id).long()
        )
        batch["token_type_ids"] = pad_for_token_type_ids(
            fake_inputs_with_labels["input_ids"], self.tokenizer, pad_to_multiple_of=self.pad_to_multiple_of
        )
        return batch

    def _generate_fake_inputs(self, examples: List[List[int]]) -> Dict[str, Any]:
        input_ids = [self.tokenizer.prepare_for_model(example, padding=False)["input_ids"] for example in examples]
        torch_masked_boolean: torch.Tensor = super().__call__(input_ids)["input_ids"]
        masked_boolean = (torch_masked_boolean == self.tokenizer.mask_token_id).tolist()
        labels = [masked for masked in torch.eq(torch_masked_boolean, self.tokenizer.mask_token_id).long().tolist()]

        def _fake_input_id(original_id):
            forbidden_ids = self.tokenizer.all_special_ids + [original_id]
            fake_id = random.randint(0, self.tokenizer.vocab_size - 1)
            while fake_id in forbidden_ids:
                fake_id = random.randint(0, self.tokenizer.vocab_size - 1)
            return fake_id

        generated_input_ids_seqs = []
        for idx in range(len(masked_boolean)):
            fake_input_ids = []
            curr_masked, curr_ids = masked_boolean[idx], input_ids[idx]
            while curr_ids and curr_masked:
                mask, ids = curr_masked.pop(0), curr_ids.pop(0)
                if mask is True:
                    fake_input_ids.append(_fake_input_id(ids))
                else:
                    fake_input_ids.append(ids)

            generated_input_ids_seqs.append(fake_input_ids)

        return {"input_ids": generated_input_ids_seqs, "labels": labels}
