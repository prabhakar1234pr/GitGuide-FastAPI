import logging

import tiktoken

from app.config import settings

logger = logging.getLogger(__name__)

# Initialize tokenizer once
# cl100k_base works well for most modern LLMs
_tokenizer = tiktoken.get_encoding("cl100k_base")
logger.debug("🔤 Initialized tiktoken tokenizer: cl100k_base")


def count_tokens(text: str) -> int:
    """Return token count for a given text."""
    return len(_tokenizer.encode(text))


def truncate_to_tokens(text: str, max_tokens: int) -> str:
    """Truncate text to at most max_tokens. Returns original if within limit."""
    if not text:
        return text
    tokens = _tokenizer.encode(text)
    if len(tokens) <= max_tokens:
        return text
    return _tokenizer.decode(tokens[:max_tokens])


def chunk_text(
    *,
    project_id: str,
    file_path: str,
    content: str,
    language: str,
) -> list[dict]:
    """
    Split a single file into token-based chunks.

    Returns a list of chunks with metadata ready for DB insertion.
    """

    chunk_size = settings.chunk_size  # e.g. 500
    chunk_overlap = settings.chunk_overlap  # e.g. 100

    tokens = _tokenizer.encode(content)
    total_tokens = len(tokens)

    chunks: list[dict] = []
    start = 0
    chunk_index = 0

    while start < total_tokens:
        end = start + chunk_size
        chunk_tokens = tokens[start:end]

        chunk_text = _tokenizer.decode(chunk_tokens)
        token_count = len(chunk_tokens)

        chunks.append(
            {
                "project_id": project_id,
                "file_path": file_path,
                "chunk_index": chunk_index,
                "language": language,
                "content": chunk_text,
                "token_count": token_count,
            }
        )

        chunk_index += 1
        start += chunk_size - chunk_overlap

        if len(chunks) > settings.max_chunks_per_project:
            logger.error(
                f"❌ Maximum chunk limit exceeded for file {file_path}: {len(chunks)} > {settings.max_chunks_per_project}"
            )
            raise ValueError(
                f"Maximum chunk limit exceeded ({settings.max_chunks_per_project} chunks)"
            )

    return chunks


def chunk_files(
    *,
    project_id: str,
    files: list[dict[str, str]],
) -> list[dict]:
    """
    Chunk multiple files into a flat list of chunks.

    Input:
        files = [
          {
            "file_path": str,
            "content": str,
            "language": str
          }
        ]
    """
    logger.info(f"✂️  Chunking {len(files)} files for project_id={project_id}")
    logger.debug(f"   Chunk size: {settings.chunk_size} tokens")
    logger.debug(f"   Chunk overlap: {settings.chunk_overlap} tokens")
    logger.debug(f"   Max chunks per project: {settings.max_chunks_per_project}")

    all_chunks: list[dict] = []
    files_chunked = 0

    for file in files:
        file_path = file["file_path"]
        content_size = len(file["content"].encode("utf-8"))
        file_tokens = count_tokens(file["content"])

        logger.debug(
            f"   Chunking file {files_chunked + 1}/{len(files)}: {file_path} ({content_size / 1024:.1f} KB, {file_tokens:,} tokens)"
        )

        file_chunks = chunk_text(
            project_id=project_id,
            file_path=file_path,
            content=file["content"],
            language=file["language"],
        )

        all_chunks.extend(file_chunks)
        files_chunked += 1

        logger.debug(
            f"      Created {len(file_chunks)} chunks from {file_path} (total chunks so far: {len(all_chunks)})"
        )

        if len(all_chunks) > settings.max_chunks_per_project:
            logger.error(
                f"❌ Maximum chunk limit exceeded: {len(all_chunks)} > {settings.max_chunks_per_project}"
            )
            raise ValueError(
                f"Maximum chunk limit exceeded ({settings.max_chunks_per_project} chunks)"
            )

    total_tokens = sum(c["token_count"] for c in all_chunks)
    avg_chunk_size = total_tokens // len(all_chunks) if all_chunks else 0

    logger.info(f"✅ Chunked {files_chunked} files into {len(all_chunks)} chunks")
    logger.debug(f"   Total tokens: {total_tokens:,}")
    logger.debug(f"   Average chunk size: {avg_chunk_size} tokens")
    languages_set = {f["language"] for f in files}
    files_by_lang = {lang: sum(1 for f in files if f["language"] == lang) for lang in languages_set}
    logger.debug(f"   Files by language: {files_by_lang}")

    return all_chunks
