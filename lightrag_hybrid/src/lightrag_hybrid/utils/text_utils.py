"""Utility functions for text processing, hashing, and logging."""
from __future__ import annotations

import hashlib
import logging
import re
from typing import List, Tuple

import tiktoken


def setup_logging(level: str = "INFO") -> logging.Logger:
    """Configure structured logging."""
    logger = logging.getLogger("lightrag_hybrid")
    if not logger.handlers:
        handler = logging.StreamHandler()
        formatter = logging.Formatter(
            "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    return logger


def compute_sha256(text: str) -> str:
    """Compute SHA-256 hash of text for deduplication."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def compute_md5(text: str) -> str:
    """Compute MD5 hash of text for quick comparison."""
    return hashlib.md5(text.encode("utf-8")).hexdigest()


def count_tokens(text: str, model: str = "cl100k_base") -> int:
    """Count tokens using tiktoken."""
    try:
        enc = tiktoken.get_encoding(model)
        return len(enc.encode(text))
    except Exception:
        # Fallback: approximate ~4 chars per token
        return len(text) // 4


def recursive_character_split(
    text: str,
    chunk_size: int = 512,
    chunk_overlap: int = 64,
    separators: List[str] | None = None,
) -> List[Tuple[str, int, int]]:
    """
    Recursively split text by separators until chunks fit within token limit.
    Returns list of (chunk_text, start_idx, end_idx).
    """
    if separators is None:
        separators = ["\n\n", "\n", ". ", "! ", "? ", "; ", ", ", " ", ""]

    def split_by_separator(text: str, separator: str) -> List[str]:
        if separator == "":
            return list(text)
        return text.split(separator)

    def merge_splits(splits: List[str], separator: str) -> List[Tuple[str, int, int]]:
        """Merge splits into chunks respecting chunk_size."""
        chunks: List[Tuple[str, int, int]] = []
        current_chunk: List[str] = []
        current_len = 0
        current_start = 0
        char_pos = 0

        for split in splits:
            split_len = count_tokens(split)
            separator_len = count_tokens(separator) if separator else 0

            if split_len > chunk_size:
                # Split is too big - flush current chunk first
                if current_chunk:
                    chunk_text = separator.join(current_chunk)
                    chunks.append((chunk_text, current_start, char_pos))
                    # Overlap for next chunk
                    overlap_splits = []
                    overlap_len = 0
                    for s in reversed(current_chunk):
                        s_len = count_tokens(s) + (separator_len if overlap_splits else 0)
                        if overlap_len + s_len <= chunk_overlap:
                            overlap_splits.insert(0, s)
                            overlap_len += s_len
                        else:
                            break
                    current_chunk = overlap_splits
                    current_len = overlap_len
                    current_start = char_pos - len(chunk_text) + len(separator.join(overlap_splits))

                # Recursively split the oversized chunk
                chunks.extend(recursive_split_single(split, chunk_size, chunk_overlap, char_pos))
                char_pos += len(split) + len(separator)
                continue

            if current_len + split_len + (separator_len if current_chunk else 0) > chunk_size:
                # Flush current chunk
                chunk_text = separator.join(current_chunk)
                chunks.append((chunk_text, current_start, char_pos))

                # Build overlap
                overlap_splits = []
                overlap_len = 0
                for s in reversed(current_chunk):
                    s_len = count_tokens(s) + (separator_len if overlap_splits else 0)
                    if overlap_len + s_len <= chunk_overlap:
                        overlap_splits.insert(0, s)
                        overlap_len += s_len
                    else:
                        break

                current_chunk = overlap_splits + [split]
                current_len = overlap_len + split_len + (separator_len if overlap_splits else 0)
                current_start = char_pos - len(chunk_text) + len(separator.join(overlap_splits))
            else:
                current_chunk.append(split)
                current_len += split_len + (separator_len if len(current_chunk) > 1 else 0)

            char_pos += len(split) + len(separator)

        if current_chunk:
            chunk_text = separator.join(current_chunk)
            chunks.append((chunk_text, current_start, char_pos))

        return chunks

    def recursive_split_single(text: str, chunk_size: int, chunk_overlap: int, base_pos: int) -> List[Tuple[str, int, int]]:
        """Recursively split a single piece of text."""
        for sep in separators:
            splits = split_by_separator(text, sep)
            if len(splits) > 1:
                chunks = merge_splits(splits, sep)
                # Adjust positions
                adjusted = []
                running_pos = base_pos
                for chunk_text, _, _ in chunks:
                    end_pos = running_pos + len(chunk_text)
                    adjusted.append((chunk_text, running_pos, end_pos))
                    # Account for overlap in position
                    if len(adjusted) < len(chunks):
                        overlap_text = chunk_text[-(chunk_overlap * 4):]  # rough char estimate
                        running_pos = end_pos - len(overlap_text)
                    else:
                        running_pos = end_pos
                return adjusted
        # Final fallback: hard split by character count
        char_chunk = chunk_size * 4  # rough estimate
        chunks = []
        for i in range(0, len(text), char_chunk - chunk_overlap * 4):
            chunk_text = text[i:i + char_chunk]
            chunks.append((chunk_text, base_pos + i, base_pos + i + len(chunk_text)))
        return chunks

    return merge_splits(split_by_separator(text, separators[0]), separators[0])


def clean_text(text: str) -> str:
    """Clean and normalize text."""
    # Remove excessive whitespace
    text = re.sub(r"\s+", " ", text)
    # Remove control characters except newlines
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text)
    # Normalize unicode spaces
    text = text.replace("\u00a0", " ")
    text = text.replace("\u200b", "")
    return text.strip()


def extract_snippet(text: str, max_words: int = 12) -> str:
    """Extract a brief snippet from text for citations."""
    words = text.split()
    if len(words) <= max_words:
        return text
    return " ".join(words[:max_words]) + "..."


def resolve_coreferences(query: str, history: List[Tuple[str, str]]) -> str:
    """
    Simple rule-based coreference resolution.
    In production, use an NLP model or LLM call.
    """
    # Collect named entities from history (simplified)
    # This is a placeholder - in production, use spaCy or an LLM
    if not history:
        return query

    # Simple pronoun replacement heuristic
    last_user_msg = None
    last_assistant_msg = None
    for role, content in reversed(history):
        if role == "user" and last_user_msg is None:
            last_user_msg = content
        elif role == "assistant" and last_assistant_msg is None:
            last_assistant_msg = content
        if last_user_msg and last_assistant_msg:
            break

    resolved = query
    # Very basic replacements - production would use a proper coref model
    if last_assistant_msg:
        # Extract potential entity mentions (capitalized phrases)
        # This is intentionally simple for the demo
        pass

    return resolved
