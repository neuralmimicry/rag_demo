# Refiner API Documentation

Complete API documentation for Refiner services with Swagger/OpenAPI specifications.

## Overview

Refiner provides two main services:

1. **Refiner Web Service** (Python/Flask) - AI assistant, voice integration, project management
2. **STT Service** (Rust/Axum) - High-performance speech-to-text with gesture planning

Both services now include comprehensive OpenAPI 3.0 documentation with interactive Swagger UI.

## Quick Start

### Refiner Web Service

**Start the service:**
```bash
python refiner_web.py
```

**Access API documentation (NO AUTHENTICATION REQUIRED):**
- 📖 Swagger UI: http://localhost:5001/api/docs
- 📄 OpenAPI YAML: http://localhost:5001/api/docs/openapi.yaml
- 📄 OpenAPI JSON: http://localhost:5001/api/docs/openapi.json
- ✅ Health Check: http://localhost:5001/health
- ℹ️ Version Info: http://localhost:5001/api/version

**⚠️ Important:** The API documentation endpoints listed above are **publicly accessible** and do not require authentication. This allows developers to explore the API structure, understand requirements, and plan integration before implementing authentication. However, **calling the actual API functions** (like `/api/assistant/chat`, `/api/jobs`, `/api/stt/transcribe`, etc.) **requires proper authentication** as documented for each endpoint.

**Default Configuration:**
- Host: `127.0.0.1` (set via `REFINER_HOST`)
- Port: `5001` (set via `REFINER_PORT`)

### STT Service

**Start the service:**
```bash
cd stt_rust
cargo run --release -- --model models/ggml-base.en.bin --bind 127.0.0.1:7079
```

**Access API documentation:**
- OpenAPI YAML: `stt_rust/openapi_stt.yaml`
- Health Check: http://localhost:7079/health
- Transcribe: `POST http://localhost:7079/transcribe`
- Gesture Plan: `POST http://localhost:7079/gesture-plan`

**Default Configuration:**
- Host: `127.0.0.1:7079` (set via `--bind`)
- Workers: Auto-detected (set via `--workers`)
- Max Audio: 8MB (set via `--max-audio-bytes`)

## API Documentation Features

### ✅ Best Practices Implemented

#### 1. **Complete OpenAPI 3.0.3 Specification**
- Full schema definitions with types, constraints, examples
- Comprehensive endpoint documentation
- Request/response schemas with validation rules
- Security schemes (session, bearer, token-based)

#### 2. **Interactive Swagger UI**
- Try-it-out functionality for all endpoints
- Automatic request/response validation
- Schema visualization
- Example payloads
- Authentication support

#### 3. **Detailed API Descriptions**
- Service overviews with feature lists
- Endpoint-level descriptions with usage notes
- Parameter descriptions with examples
- Response status code documentation
- Error handling documentation

#### 4. **Robust Schema Design**
- Reusable component schemas
- Type-safe definitions
- Validation constraints (min/max, regex, enum)
- Nested object support
- Array and object examples

#### 5. **Security Documentation**
- Authentication mechanisms clearly defined
- Security scheme references per endpoint
- Token-based auth for voice services
- Session cookie documentation
- **Public documentation endpoints** - No auth required for `/api/docs`, `/health`, `/api/version`
- Clear distinction between public documentation and authenticated API calls

#### 6. **Health & Monitoring**
- `/health` endpoints for service status
- Version information endpoints
- Service dependency checks
- Graceful degradation indicators

#### 7. **Developer Experience**
- Clear, concise documentation
- Practical examples for all endpoints
- Error response documentation
- Rate limiting information
- Usage notes and warnings

## Refiner Web API

### Public vs Authenticated Endpoints

The Refiner API has two categories of endpoints:

#### 🔓 Public Endpoints (No Authentication Required)

These endpoints are accessible without authentication to facilitate API exploration and integration planning:

```http
GET /api/docs              # Swagger UI interface
GET /api/docs/openapi.yaml # OpenAPI spec (YAML)
GET /api/docs/openapi.json # OpenAPI spec (JSON)
GET /health                # Health check
GET /api/version           # Version information
```

**Use cases:**
- Explore API capabilities before integration
- Generate client SDKs from OpenAPI spec
- Monitor service health
- Validate API compatibility

#### 🔒 Authenticated Endpoints (Authentication Required)

All functional API endpoints require authentication (session cookie or bearer token):

```http
POST /api/assistant/chat      # Requires auth
POST /api/stt/transcribe      # Requires auth
GET  /api/jobs                # Requires auth
POST /api/rag/documents       # Requires auth
# ... and all other /api/* endpoints
```

### Core Endpoints

#### Health & Version (Public)
```http
GET /health
GET /api/version
```

#### Authentication (Public for login, requires auth for logout)
```http
POST /auth/login    # Public
POST /auth/logout   # Requires auth
```

#### AI Assistant
```http
POST /api/assistant/chat
```
- Context-aware responses with RAG
- LLM provider selection (OpenAI, Gemini, Ollama)
- Temperature and token control
- Session continuity

#### Speech-to-Text
```http
POST /api/stt/transcribe
POST /api/stt/gesture-plan
```
- Multi-format audio support (WAV, MP3, OGG, WEBM, FLAC)
- Multi-language transcription
- Gesture/motion generation (BSL, gesticulation)
- Audio analysis and speaker detection

#### Job Management
```http
GET /api/jobs
POST /api/jobs
GET /api/jobs/{job_id}
```

#### RAG Document Management
```http
POST /api/rag/documents
GET /api/rag/documents
DELETE /api/rag/documents/{doc_id}
```

### Authentication

**Session-based (Web UI):**
```bash
curl -X POST http://localhost:5001/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username": "admin", "password": "secret"}' \
  -c cookies.txt

curl http://localhost:5001/api/assistant/chat \
  -b cookies.txt \
  -H "Content-Type: application/json" \
  -d '{"message": "Hello, assistant!"}'
```

**Bearer Token:**
```bash
curl http://localhost:5001/api/assistant/chat \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"message": "Hello!"}'
```

## STT Service API

### Core Endpoints

#### Health Check
```http
GET /health
```

#### Transcribe Audio
```http
POST /transcribe
Content-Type: multipart/form-data

- audio: (binary) Audio file
- lang: (optional) Language code (e.g., en-GB)
- prompt: (optional) Context prompt for accuracy
- gesture_mode: (optional) gesticulation | bsl
- avatar_mode: (optional) chat | office
- collaboration_mode: (optional) true | false
```

#### Generate Gesture Plan
```http
POST /gesture-plan
Content-Type: application/json

{
  "text": "Hello, welcome to NeuralMimicry!",
  "gesture_mode": "gesticulation",
  "avatar_mode": "chat"
}
```

### Example: Transcribe Audio

```bash
# Basic transcription
curl -X POST http://localhost:7079/transcribe \
  -F "audio=@recording.wav" \
  -F "lang=en-GB"

# With gesture planning and collaboration mode
curl -X POST http://localhost:7079/transcribe \
  -F "audio=@meeting.mp3" \
  -F "lang=en-GB" \
  -F "gesture_mode=gesticulation" \
  -F "avatar_mode=office" \
  -F "collaboration_mode=true" \
  -F "prompt=NeuralMimicry AARNN Paul Isaac's Tracey"
```

### Example: Generate Gestures

```bash
curl -X POST http://localhost:7079/gesture-plan \
  -H "Content-Type: application/json" \
  -d '{
    "text": "Tell me about AARNN and NeuralMimicry security.",
    "gesture_mode": "bsl",
    "avatar_mode": "office"
  }'
```

### Response Format

**Transcription Response:**
```json
{
  "status": "ok",
  "text": "Tell me about AARNN and NeuralMimicry security.",
  "lang": "en-GB",
  "gesture_mode": "gesticulation",
  "avatar_mode": "chat",
  "avatar_motion": {
    "duration_ms": 2450,
    "keyframes": [
      {
        "t": 0,
        "pose": {
          "headYaw": 0.0,
          "headPitch": 0.03,
          "leftShoulderPitch": 0.16,
          "rightShoulderPitch": 0.16,
          ...
        }
      }
    ]
  },
  "gesture_summary": {
    "style": "semantic_gesticulation",
    "token_count": 8
  },
  "audio_analysis": {
    "speech_ratio": 0.68,
    "speech_confidence": 0.82,
    "noise_level": "low",
    "snr_db": 13.0,
    "speaker_count_estimate": 1,
    "collaboration_likely": false
  }
}
```

## Configuration

### Refiner Web Service

**Environment Variables:**
```bash
# Server
export REFINER_HOST=0.0.0.0
export REFINER_PORT=5555
export REFINER_DEBUG=0

# STT Integration
export REFINER_STT_BACKEND=server
export REFINER_STT_SERVER_URL=http://localhost:7079
export REFINER_STT_GESTURE_ENABLED=1
export REFINER_STT_BSL_ENABLED=1

# Authentication
export REFINER_SECRET_KEY=your-secret-key
export REFINER_REQUIRE_SECRET_KEY=1

# LLM Providers
export OPENAI_API_KEY=sk-...
export GEMINI_API_KEY=...
```

### STT Rust Service

**Command-line Options:**
```bash
cargo run --release -- \
  --model models/ggml-base.en.bin \
  --bind 0.0.0.0:7079 \
  --lang en-GB \
  --threads 4 \
  --workers 8 \
  --max-audio-bytes 8000000 \
  --translate false
```

**Environment Variables:**
```bash
# Gesture Configuration
export REFINER_STT_GESTURE_ENABLED=1
export REFINER_STT_BSL_ENABLED=1
export REFINER_STT_GESTURE_DEFAULT_MODE=gesticulation
export REFINER_STT_GESTURE_DEFAULT_AVATAR_MODE=chat

# Prompts
export REFINER_STT_BUILTIN_CONTEXT_ENABLED=1
export REFINER_STT_BUILTIN_CONTEXT_PROMPT="NeuralMimicry terminology..."
export REFINER_STT_PROMPT_ALLOW_CLIENT=0

# Features
export REFINER_STT_CANONICALIZE_ENTITIES=1
export REFINER_STT_COLLABORATION_DEFAULT=0
```

## Advanced Features

### Multi-Speaker Detection

The STT service includes collaboration mode for multi-speaker scenarios:

```bash
curl -X POST http://localhost:7079/transcribe \
  -F "audio=@meeting.wav" \
  -F "collaboration_mode=true"
```

**Response includes:**
- Speaker segments with timestamps
- Speaker turn detection
- Speaker count estimation
- Confidence scores per segment

### Entity Canonicalization

Automatic correction of NeuralMimicry terminology:
- `aaron` → `AARNN`
- `tracy` → `Tracey`
- `isaac`/`isaacs` → `Isaac's`
- Common name variations

### Gesture Modes

**Gesticulation** (conversational):
- Semantic intent classification (greeting, question, affirm, etc.)
- Natural conversation gestures
- Lower amplitude, subtle movements

**BSL** (British Sign Language):
- Formal signing motions
- Fingerspelling for unknown words
- Higher amplitude, precise movements

### Avatar Modes

- **Chat**: Casual mode with 50-74% amplitude
- **Office**: Professional mode with 76-100% amplitude

## Error Handling

### Common Error Responses

**Refiner Web:**
```json
{
  "error": "Invalid request",
  "details": "Missing required field: message",
  "code": "INVALID_INPUT"
}
```

**STT Service:**
```json
{
  "error": "audio_too_large"
}
```

### HTTP Status Codes

| Code | Meaning |
|------|---------|
| 200 | Success |
| 400 | Bad Request (invalid parameters) |
| 401 | Unauthorized (authentication required) |
| 413 | Payload Too Large (file size exceeded) |
| 429 | Rate Limit Exceeded |
| 500 | Internal Server Error |
| 503 | Service Unavailable (capacity/dependency issue) |

## Development

### Install Dependencies

```bash
# Python dependencies
pip install -r requirements.txt

# Rust dependencies (for STT service)
cd stt_rust
cargo build --release
```

### Run Tests

```bash
# Python tests
pytest tests/

# Rust tests
cd stt_rust
cargo test
```

### Update API Documentation

1. Edit OpenAPI specifications:
   - `openapi_refiner.yaml` (Refiner Web)
   - `stt_rust/openapi_stt.yaml` (STT Service)

2. Restart services to reload specs

3. Verify at `/api/docs`

## Monitoring & Observability

### Health Checks

**Refiner Web:**
```bash
curl http://localhost:5001/health
```

**STT Service:**
```bash
curl http://localhost:7079/health
```

### Metrics

Refiner exposes Prometheus metrics at `/metrics` (if enabled).

### Logging

Configure log levels via environment:
```bash
export RUST_LOG=info  # STT service
export REFINER_LOG_LEVEL=INFO  # Refiner web
```

## Security Best Practices

1. **Authentication**: Always use authentication in production
2. **HTTPS**: Enable TLS for production deployments
3. **Rate Limiting**: Configure rate limits per endpoint
4. **Input Validation**: All inputs are validated against schemas
5. **File Size Limits**: Audio uploads have configurable size limits
6. **Secret Management**: Use environment variables for secrets
7. **Audit Logging**: Enabled by default for sensitive operations

## Troubleshooting

### Swagger UI Not Loading

**Check:**
1. OpenAPI spec is valid YAML
2. Flask app is running
3. Access correct URL: `/api/docs`

### STT Service Unavailable

**Check:**
1. Service is running: `curl http://localhost:7079/health`
2. Port is correct
3. Firewall rules allow connection
4. Model file is accessible

### Authentication Errors

**Check:**
1. Valid credentials/token provided
2. Session cookie not expired
3. CORS headers configured (if cross-origin)

## Support

- **Documentation**: http://localhost:5001/api/docs
- **Issues**: https://github.com/neuralmimicry/refiner/issues
- **Contact**: support@neuralmimicry.ai

## License

Proprietary - NeuralMimicry Ltd.
