"""Processor and tiny synthetic checkpoint proof; no ASR or CUDA initialization."""
import argparse
import json
import tempfile


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--processor-dir", required=True)
    args = parser.parse_args()
    import numpy as np
    import torch
    from transformers import AutoProcessor, Qwen3ASRConfig, Qwen3ASRForConditionalGeneration

    assert not torch.cuda.is_initialized()
    torch.set_num_threads(2)
    torch.manual_seed(0)
    processor = AutoProcessor.from_pretrained(
        args.processor_dir, local_files_only=True, trust_remote_code=False)
    assert type(processor).__module__ == "transformers.models.qwen3_asr.processing_qwen3_asr"
    observed = []
    for samples in (16000, 480000):
        inputs = processor.apply_transcription_request(
            audio=np.zeros(samples, dtype=np.float32), language=None, prompt=None,
            return_tensors="pt", audio_kwargs={"sampling_rate": 16000, "truncation": False})
        assert inputs["input_ids"].shape[0] == inputs["input_features"].shape[0] == 1
        audio_tokens = int((inputs["input_ids"] == 151676).sum().item())
        assert audio_tokens > 0
        assert set(inputs) <= {"input_ids", "attention_mask", "input_features", "input_features_mask"}
        assert inputs["input_features_mask"].sum().item() == samples // 160
        observed.append({"samples": samples, "audio_tokens": audio_tokens,
                         "mask_frames": int(inputs["input_features_mask"].sum().item())})

    config = Qwen3ASRConfig(
        audio_config={"d_model": 16, "encoder_layers": 1, "encoder_attention_heads": 2,
                      "encoder_ffn_dim": 32, "downsample_hidden_size": 8, "output_dim": 16,
                      "max_position_embeddings": 13, "num_mel_bins": 128},
        text_config={"hidden_size": 16, "intermediate_size": 32, "num_hidden_layers": 1,
                     "num_attention_heads": 2, "num_key_value_heads": 1, "head_dim": 8,
                     "vocab_size": 128, "max_position_embeddings": 128},
        audio_token_id=100, timestamp_token_id=101, eos_token_id=[1, 2], pad_token_id=0)
    model = Qwen3ASRForConditionalGeneration(config).to(dtype=torch.bfloat16)
    assert sum(p.numel() for p in model.parameters()) < 100000
    expected = {key: value.clone() for key, value in model.state_dict().items()}
    with tempfile.TemporaryDirectory(prefix="qwen-synthetic-") as directory:
        model.save_pretrained(directory, safe_serialization=True)
        loaded, info = Qwen3ASRForConditionalGeneration.from_pretrained(
            directory, dtype=torch.bfloat16, device_map={"": "cpu"},
            use_safetensors=True, use_kernels=False,
            attn_implementation="sdpa", output_loading_info=True,
            local_files_only=True, trust_remote_code=False)
        assert not any(info.get(key) for key in (
            "missing_keys", "unexpected_keys", "mismatched_keys", "error_msgs"))
        assert all(not p.is_meta for p in loaded.parameters())
        assert all(not p.is_meta for p in loaded.buffers())
        assert set(expected) == set(loaded.state_dict())
        assert all(torch.equal(expected[key], value) for key, value in loaded.state_dict().items())
    assert not torch.cuda.is_initialized()
    print(json.dumps({"status": "passed", "synthetic_checkpoint_roundtrip": True,
                      "processor_inputs": observed, "asr_calls": 0, "cuda_initialized": False}))


if __name__ == "__main__":
    main()
