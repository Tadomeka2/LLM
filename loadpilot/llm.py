"""Local Hugging Face model wrapper.

The model is downloaded and executed locally with ``transformers``; no
inference API and no API key are used. The wrapper is intentionally thin so
the agent loop stays testable with a fake LLM.
"""

from __future__ import annotations

import logging
import time
from typing import Protocol

from . import config

log = logging.getLogger(__name__)


class ChatModel(Protocol):
    def chat(self, messages: list[dict]) -> str: ...


class LocalLLM:
    """A causal LM loaded from Hugging Face and run on the local device."""

    def __init__(
        self,
        model_id: str = config.DEFAULT_MODEL_ID,
        *,
        device: str | None = None,
        load_in_4bit: bool = False,
        max_new_tokens: int = config.MAX_NEW_TOKENS,
        temperature: float = 0.1,
        verbose: bool = True,
    ) -> None:
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer

        self._torch = torch
        self.model_id = model_id
        self.max_new_tokens = max_new_tokens
        self.temperature = temperature
        self.verbose = verbose

        self.device = self._pick_device(device)
        if load_in_4bit and not self.device.startswith("cuda"):
            raise ValueError("4-bit loading requires a CUDA GPU; omit --4bit on CPU/MPS.")
        if verbose:
            print(f"[llm] loading {model_id} on {self.device} ...", flush=True)

        started = time.time()
        self.tokenizer = AutoTokenizer.from_pretrained(model_id)
        if self.tokenizer.pad_token_id is None:
            self.tokenizer.pad_token = self.tokenizer.eos_token

        dtype = torch.float16 if self.device.startswith(("cuda", "mps")) else torch.float32
        load_kwargs: dict = {}
        if load_in_4bit:
            from transformers import BitsAndBytesConfig

            load_kwargs["quantization_config"] = BitsAndBytesConfig(
                load_in_4bit=True,
                bnb_4bit_quant_type="nf4",
                bnb_4bit_compute_dtype=torch.float16,
                bnb_4bit_use_double_quant=True,
            )
            load_kwargs["device_map"] = "auto"

        try:
            self.model = AutoModelForCausalLM.from_pretrained(model_id, dtype=dtype, **load_kwargs)
        except TypeError:
            # transformers < 4.56 uses torch_dtype instead of dtype.
            self.model = AutoModelForCausalLM.from_pretrained(
                model_id, torch_dtype=dtype, **load_kwargs
            )

        if not load_in_4bit:
            self.model.to(self.device)
        self.model.eval()
        self.input_device = next(self.model.parameters()).device
        if verbose:
            print(f"[llm] ready in {time.time() - started:.1f}s", flush=True)

    @staticmethod
    def _pick_device(requested: str | None) -> str:
        import torch

        if requested:
            return requested
        if torch.cuda.is_available():
            return "cuda"
        if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
            return "mps"
        return "cpu"

    def chat(self, messages: list[dict]) -> str:
        """Generate the next assistant message from the chat history."""

        prompt = self.tokenizer.apply_chat_template(
            messages,
            tokenize=False,
            add_generation_prompt=True,
        )
        inputs = self.tokenizer(prompt, return_tensors="pt")
        inputs = {key: value.to(self.input_device) for key, value in inputs.items()}

        generate_kwargs: dict = {
            "max_new_tokens": self.max_new_tokens,
            "repetition_penalty": 1.05,
            "pad_token_id": self.tokenizer.pad_token_id,
            "eos_token_id": self.tokenizer.eos_token_id,
        }
        if self.temperature > 0:
            generate_kwargs.update(
                do_sample=True,
                temperature=self.temperature,
                top_p=0.9,
            )
        else:
            generate_kwargs["do_sample"] = False

        with self._torch.inference_mode():
            output = self.model.generate(**inputs, **generate_kwargs)

        prompt_length = inputs["input_ids"].shape[-1]
        new_tokens = output[0][prompt_length:]
        return self.tokenizer.decode(new_tokens, skip_special_tokens=True).strip()

    def close(self) -> None:
        del self.model
        if self.device.startswith("cuda"):
            self._torch.cuda.empty_cache()
