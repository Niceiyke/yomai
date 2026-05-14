# Multi-Modal Input

Agent endpoints accept images, audio, and document content alongside text.

## Content Block Format

Pass a content array instead of a plain string:

```json
{
  "message": [
    {"type": "text", "text": "Describe this image"},
    {"type": "image_url", "image_url": {"url": "https://example.com/photo.png"}}
  ]
}
```

## Supported Content Types

| Type | Block Format | Notes |
|---|---|---|
| `text` | `{"type": "text", "text": "..."}` | Plain text block |
| `image_url` | `{"type": "image_url", "image_url": {"url": "https://..."}}` | URL or `data:` URI |
| `image` | `{"type": "image", "source": {"media_type": "image/png", "data": "..."}}` | Base64-encoded binary |
| `input_audio` | `{"type": "input_audio", "input_audio": {"data": "...", "format": "wav"}}` | Base64 audio data |
| `document_url` | `{"type": "document_url", "document_url": {"url": "https://..."}}` | Remote document URL |
| `document` | `{"type": "document", "source": {"media_type": "application/pdf", "data": "..."}}` | Base64 document content |

## Provider Compatibility

Content blocks are automatically normalised to each provider's native format:

- **Anthropic**: Images → `{"type": "image", "source": {...}}`; documents → text placeholder
- **OpenAI**: Images → `{"type": "image_url", "image_url": {...}}`; documents → text placeholder
- **Gemini**: Images/audio → `{"inline_data": {...}}`; documents → text placeholder
- **Mistral / Groq / vLLM**: Follow OpenAI format

Non-image documents (PDFs, etc.) are rendered as text placeholders by default. Providers that natively support document processing (e.g. Gemini) will handle them appropriately.

## Plain Text Extraction

For hooks, graphs, and memory storage, a plain-text preview is extracted from content blocks. Image and audio blocks are represented as `[image]` / `[audio]` / `[document]` tags.
