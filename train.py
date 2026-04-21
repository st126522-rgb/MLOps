"""
Fine-tune a token-classification model from reviewed local labels.

Input:
  local_data/labeled/train_set.json

Output:
  local_data/models/candidate/
"""

import argparse
import datetime
import json
import shutil
from pathlib import Path

import torch
from torch.utils.data import DataLoader, Dataset
from transformers import AutoModelForTokenClassification, AutoTokenizer

from config import ENTITY_TYPES, LOCAL_MODE, NER_MODEL
from s3_utils import key_exists, read_json, storage_path, upload_directory


LABELS = ["O"] + [f"{prefix}-{entity_type}" for entity_type in ENTITY_TYPES for prefix in ("B", "I")]
LABEL_TO_ID = {label: index for index, label in enumerate(LABELS)}
ID_TO_LABEL = {index: label for label, index in LABEL_TO_ID.items()}


def load_samples(path: Path, max_samples: int | None = None) -> list[dict]:
    if path.exists():
        samples = json.loads(path.read_text(encoding="utf-8"))
    elif not LOCAL_MODE and key_exists("labeled/train_set.json"):
        samples = read_json("labeled/train_set.json")
    else:
        raise SystemExit(f"Training set not found: {path}. Run `python label_review.py build-datasets` first.")
    if max_samples:
        samples = samples[:max_samples]
    if not samples:
        raise SystemExit("Training set is empty. Accept or correct label-review rows first.")
    return samples


def entity_spans(text: str, entities: list[dict]) -> list[tuple[int, int, str]]:
    spans = []
    lower_text = text.lower()
    for entity in entities:
        value = entity.get("entity", "").strip()
        entity_type = entity.get("type", "").strip()
        if not value or entity_type not in ENTITY_TYPES:
            continue
        start = lower_text.find(value.lower())
        if start < 0:
            continue
        spans.append((start, start + len(value), entity_type))
    return spans


class NerDataset(Dataset):
    def __init__(self, samples: list[dict], tokenizer, max_length: int):
        self.items = []
        for sample in samples:
            text = sample["text"]
            encoding = tokenizer(
                text,
                max_length=max_length,
                padding="max_length",
                truncation=True,
                return_offsets_mapping=True,
            )
            labels = []
            spans = entity_spans(text, sample.get("entities", []))
            for start, end in encoding.pop("offset_mapping"):
                if start == end:
                    labels.append(-100)
                    continue
                label = "O"
                for entity_start, entity_end, entity_type in spans:
                    if start < entity_end and end > entity_start:
                        label = f"{'B' if start <= entity_start else 'I'}-{entity_type}"
                        break
                labels.append(LABEL_TO_ID[label])

            self.items.append(
                {
                    "input_ids": torch.tensor(encoding["input_ids"], dtype=torch.long),
                    "attention_mask": torch.tensor(encoding["attention_mask"], dtype=torch.long),
                    "labels": torch.tensor(labels, dtype=torch.long),
                }
            )

    def __len__(self) -> int:
        return len(self.items)

    def __getitem__(self, index: int) -> dict:
        return self.items[index]


def train(args) -> Path:
    train_path = storage_path("labeled/train_set.json")
    samples = load_samples(train_path, max_samples=args.max_samples)

    tokenizer = AutoTokenizer.from_pretrained(args.base_model)
    dataset = NerDataset(samples, tokenizer, max_length=args.max_length)
    loader = DataLoader(dataset, batch_size=args.batch_size, shuffle=True)

    model = AutoModelForTokenClassification.from_pretrained(
        args.base_model,
        num_labels=len(LABELS),
        id2label=ID_TO_LABEL,
        label2id=LABEL_TO_ID,
        ignore_mismatched_sizes=True,
    )

    device = torch.device("cuda" if torch.cuda.is_available() and not args.cpu else "cpu")
    model.to(device)
    optimizer = torch.optim.AdamW(model.parameters(), lr=args.learning_rate)

    model.train()
    for epoch in range(args.epochs):
        total_loss = 0.0
        for batch in loader:
            batch = {key: value.to(device) for key, value in batch.items()}
            outputs = model(**batch)
            loss = outputs.loss
            loss.backward()
            optimizer.step()
            optimizer.zero_grad()
            total_loss += loss.item()
        print(f"[TRAIN] epoch={epoch + 1} loss={total_loss / max(1, len(loader)):.4f}")

    output_dir = storage_path(args.output_prefix)
    if output_dir.exists():
        if not args.overwrite:
            raise SystemExit(f"Output already exists: {output_dir}. Use --overwrite to replace it.")
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    model.save_pretrained(output_dir)
    tokenizer.save_pretrained(output_dir)
    metadata = {
        "created_at": datetime.datetime.now(datetime.UTC).isoformat(),
        "base_model": args.base_model,
        "train_samples": len(samples),
        "labels": LABELS,
    }
    (output_dir / "training_metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    print(f"[OK] Saved candidate model -> {output_dir}")
    if not LOCAL_MODE:
        uploaded = upload_directory(output_dir, args.output_prefix)
        print(f"[OK] Uploaded candidate model to s3://{args.bucket}/{args.output_prefix} ({uploaded} files)")
    return output_dir


def main() -> None:
    parser = argparse.ArgumentParser(description="Fine-tune a local candidate NER model.")
    parser.add_argument("--base-model", default=NER_MODEL)
    parser.add_argument("--output-prefix", default="models/candidate")
    parser.add_argument("--epochs", type=int, default=1)
    parser.add_argument("--batch-size", type=int, default=4)
    parser.add_argument("--max-length", type=int, default=160)
    parser.add_argument("--learning-rate", type=float, default=5e-5)
    parser.add_argument("--max-samples", type=int, default=None)
    parser.add_argument("--cpu", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--bucket", default=None, help="Optional bucket label for logs when LOCAL_MODE=false.")
    args = parser.parse_args()
    if args.bucket is None:
        from config import BUCKET

        args.bucket = BUCKET
    train(args)


if __name__ == "__main__":
    main()
