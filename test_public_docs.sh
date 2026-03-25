#!/bin/bash
# Test script to verify API documentation endpoints are publicly accessible

set -e

HOST="${REFINER_HOST:-localhost}"
PORT="${REFINER_PORT:-5001}"
BASE_URL="http://${HOST}:${PORT}"

echo "Testing Refiner API Documentation Public Access"
echo "================================================"
echo ""
echo "Base URL: ${BASE_URL}"
echo ""

# Test health endpoint
echo "✓ Testing /health (should be public)..."
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" "${BASE_URL}/health")
if [ "$HTTP_CODE" = "200" ]; then
    echo "  ✅ SUCCESS - Health endpoint is publicly accessible (HTTP $HTTP_CODE)"
    curl -s "${BASE_URL}/health" | jq . 2>/dev/null || echo "  Response received"
else
    echo "  ❌ FAILED - Health endpoint returned HTTP $HTTP_CODE (expected 200)"
fi
echo ""

# Test version endpoint
echo "✓ Testing /api/version (should be public)..."
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" "${BASE_URL}/api/version")
if [ "$HTTP_CODE" = "200" ]; then
    echo "  ✅ SUCCESS - Version endpoint is publicly accessible (HTTP $HTTP_CODE)"
    curl -s "${BASE_URL}/api/version" | jq . 2>/dev/null || echo "  Response received"
else
    echo "  ❌ FAILED - Version endpoint returned HTTP $HTTP_CODE (expected 200)"
fi
echo ""

# Test Swagger UI
echo "✓ Testing /api/docs (should be public)..."
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" "${BASE_URL}/api/docs")
if [ "$HTTP_CODE" = "200" ]; then
    echo "  ✅ SUCCESS - Swagger UI is publicly accessible (HTTP $HTTP_CODE)"
else
    echo "  ❌ FAILED - Swagger UI returned HTTP $HTTP_CODE (expected 200)"
fi
echo ""

# Test OpenAPI JSON spec
echo "✓ Testing /api/docs/openapi.json (should be public)..."
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" "${BASE_URL}/api/docs/openapi.json")
if [ "$HTTP_CODE" = "200" ]; then
    echo "  ✅ SUCCESS - OpenAPI JSON spec is publicly accessible (HTTP $HTTP_CODE)"
    echo "  Checking if valid JSON..."
    if curl -s "${BASE_URL}/api/docs/openapi.json" | jq -e '.openapi' > /dev/null 2>&1; then
        echo "  ✅ Valid OpenAPI JSON"
        VERSION=$(curl -s "${BASE_URL}/api/docs/openapi.json" | jq -r '.info.version')
        TITLE=$(curl -s "${BASE_URL}/api/docs/openapi.json" | jq -r '.info.title')
        echo "  📋 Title: $TITLE"
        echo "  📦 Version: $VERSION"
    else
        echo "  ⚠️  Response is not valid JSON"
    fi
else
    echo "  ❌ FAILED - OpenAPI JSON spec returned HTTP $HTTP_CODE (expected 200)"
fi
echo ""

# Test OpenAPI YAML spec
echo "✓ Testing /api/docs/openapi.yaml (should be public)..."
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" "${BASE_URL}/api/docs/openapi.yaml")
if [ "$HTTP_CODE" = "200" ]; then
    echo "  ✅ SUCCESS - OpenAPI YAML spec is publicly accessible (HTTP $HTTP_CODE)"
else
    echo "  ❌ FAILED - OpenAPI YAML spec returned HTTP $HTTP_CODE (expected 200)"
fi
echo ""

# Test that authenticated endpoints ARE protected
echo "✓ Testing /api/assistant/chat (should require auth)..."
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" -X POST \
    -H "Content-Type: application/json" \
    -d '{"message":"test"}' \
    "${BASE_URL}/api/assistant/chat")
if [ "$HTTP_CODE" = "401" ] || [ "$HTTP_CODE" = "403" ]; then
    echo "  ✅ SUCCESS - Assistant endpoint is protected (HTTP $HTTP_CODE)"
else
    echo "  ⚠️  WARNING - Assistant endpoint returned HTTP $HTTP_CODE (expected 401 or 403)"
fi
echo ""

echo "================================================"
echo "Public API Documentation Access Test Complete!"
echo ""
echo "📖 Access Swagger UI at: ${BASE_URL}/api/docs"
echo ""
