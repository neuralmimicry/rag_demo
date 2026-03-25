# BSL (British Sign Language) Integration Guide

## Overview

The STT service provides **separate text outputs** for **gesticulation** and **BSL (British Sign Language)** modes, reflecting the fundamental differences in grammar and word order between English and BSL.

## Key Concept: Two Text Representations

When `gesture_mode` is set to `bsl`, the API response includes **both**:

1. **`text`** - Original English transcription (standard English word order)
2. **`bsl_text`** - BSL-transformed text (BSL grammar and word order)

### Why Two Representations?

BSL is **not English on hands**. It has its own grammar, syntax, and word order that differs significantly from spoken/written English:

| Feature | English | BSL |
|---------|---------|-----|
| Word order | Subject-Verb-Object | Topic-Comment |
| Articles | Uses "a", "an", "the" | No articles |
| "To be" verbs | Uses "is", "are", "was", "were" | No "to be" verbs |
| Questions | Question word at start | Question word at end |
| Time indicators | Flexible position | Always at start |
| Auxiliary verbs | Uses "do", "does", "did" | No auxiliaries |

## API Response Structure

### Gesticulation Mode

```json
{
  "status": "ok",
  "text": "What is your name?",
  "gesture_mode": "gesticulation",
  "avatar_mode": "chat",
  "bsl_text": null,
  "avatar_motion": { ... },
  "gesture_timeline": [
    {"word": "What", "start_ms": 0, "end_ms": 280},
    {"word": "is", "start_ms": 304, "end_ms": 490},
    {"word": "your", "start_ms": 514, "end_ms": 750},
    {"word": "name", "start_ms": 774, "end_ms": 1100}
  ]
}
```

**Frontend behavior:**
- Display: `text` field
- Sync with: `gesture_timeline` using `text` words
- Animation style: Natural conversational gestures

### BSL Mode

```json
{
  "status": "ok",
  "text": "What is your name?",
  "gesture_mode": "bsl",
  "avatar_mode": "office",
  "bsl_text": "your name What",
  "avatar_motion": { ... },
  "gesture_timeline": [
    {"word": "your", "start_ms": 0, "end_ms": 420},
    {"word": "name", "start_ms": 444, "end_ms": 840},
    {"word": "What", "start_ms": 864, "end_ms": 1240}
  ]
}
```

**Frontend behavior:**
- Display: `bsl_text` field (NOT `text`)
- Sync with: `gesture_timeline` using `bsl_text` words
- Animation style: BSL signing motions
- Show both texts: Display `bsl_text` prominently, optionally show `text` as subtitle/caption

## BSL Grammar Transformations

The STT service automatically transforms English text to BSL word order:

### 1. Remove Articles

```
English: "The cat is on the mat."
BSL:     "cat on mat"
```

### 2. Remove "To Be" Verbs

```
English: "I am a teacher."
BSL:     "I teacher"

English: "She is happy."
BSL:     "She happy"
```

### 3. Move Time Indicators to Start

```
English: "I went to the shop yesterday."
BSL:     "yesterday I went shop"

English: "We will meet tomorrow."
BSL:     "tomorrow we meet"
```

### 4. Move Question Words to End

```
English: "What is your name?"
BSL:     "your name What"

English: "Where are you going?"
BSL:     "you going Where"

English: "Why did you leave?"
BSL:     "you leave Why"
```

### 5. Remove Auxiliary Verbs in Questions

```
English: "Do you like coffee?"
BSL:     "you like coffee"

English: "Did you see that?"
BSL:     "you see that"
```

### 6. Complex Example

```
English: "What time are you going to the meeting tomorrow?"
BSL:     "tomorrow you going meeting time What"
         ^time     ^subject ^verb  ^object  ^question
```

## Frontend Integration

### React/TypeScript Example

```typescript
interface SttResponse {
  status: string;
  text: string;           // Always English word order
  gesture_mode: 'gesticulation' | 'bsl';
  avatar_mode: 'chat' | 'office';
  bsl_text?: string;      // Only present when gesture_mode is 'bsl'
  avatar_motion?: AvatarMotion;
  gesture_timeline?: GestureTimelineEntry[];
}

interface GestureTimelineEntry {
  word: string;
  intent: string;
  template: string;
  start_ms: number;
  end_ms: number;
}

function TranscriptDisplay({ response }: { response: SttResponse }) {
  // Select display text based on mode
  const displayText = response.gesture_mode === 'bsl'
    ? response.bsl_text
    : response.text;

  // Timeline words match display text
  const words = displayText.split(' ');

  return (
    <div className="transcript">
      {/* Primary display: BSL text in BSL mode, English in gesticulation mode */}
      <div className="primary-text">
        {displayText}
      </div>

      {/* Optional: Show English subtitle in BSL mode */}
      {response.gesture_mode === 'bsl' && (
        <div className="subtitle-text">
          English: {response.text}
        </div>
      )}

      {/* Word-by-word synchronized display */}
      <div className="word-timeline">
        {response.gesture_timeline?.map((entry, idx) => (
          <span
            key={idx}
            className="word"
            data-start={entry.start_ms}
            data-end={entry.end_ms}
          >
            {entry.word}
          </span>
        ))}
      </div>
    </div>
  );
}

// Avatar synchronization
function AvatarController({ response }: { response: SttResponse }) {
  const [currentTime, setCurrentTime] = useState(0);

  // Find current word based on timeline
  const currentWord = response.gesture_timeline?.find(
    entry => currentTime >= entry.start_ms && currentTime < entry.end_ms
  );

  useEffect(() => {
    // Play avatar motion
    if (response.avatar_motion) {
      playAvatarMotion(response.avatar_motion, (time) => {
        setCurrentTime(time);
      });
    }
  }, [response]);

  return (
    <Avatar
      motion={response.avatar_motion}
      highlightWord={currentWord?.word}
      mode={response.gesture_mode}
    />
  );
}
```

### Vue.js Example

```vue
<template>
  <div class="transcript-viewer">
    <!-- Display appropriate text -->
    <div class="primary-text">
      {{ displayText }}
    </div>

    <!-- English subtitle in BSL mode -->
    <div v-if="isBslMode" class="subtitle">
      English: {{ response.text }}
    </div>

    <!-- Word-by-word sync -->
    <div class="words">
      <span
        v-for="(entry, idx) in response.gesture_timeline"
        :key="idx"
        :class="{ active: isWordActive(entry) }"
        class="word"
      >
        {{ entry.word }}
      </span>
    </div>

    <!-- Avatar -->
    <avatar-component
      :motion="response.avatar_motion"
      :gesture-mode="response.gesture_mode"
      :current-time="currentTime"
    />
  </div>
</template>

<script>
export default {
  props: ['response'],
  data() {
    return {
      currentTime: 0
    };
  },
  computed: {
    isBslMode() {
      return this.response.gesture_mode === 'bsl';
    },
    displayText() {
      return this.isBslMode
        ? this.response.bsl_text
        : this.response.text;
    }
  },
  methods: {
    isWordActive(entry) {
      return this.currentTime >= entry.start_ms &&
             this.currentTime < entry.end_ms;
    }
  }
};
</script>
```

## Important Guidelines

### ✅ DO

1. **Use `bsl_text` for display in BSL mode**
   ```javascript
   const displayText = mode === 'bsl' ? response.bsl_text : response.text;
   ```

2. **Synchronize with `gesture_timeline`**
   - The timeline words match the display text (BSL or English)
   - Use `start_ms` and `end_ms` for word highlighting

3. **Show both texts when helpful**
   ```html
   <div class="bsl-text">{{ bsl_text }}</div>
   <div class="english-subtitle">{{ text }}</div>
   ```

4. **Respect the mode throughout the UI**
   - Avatar animation intensity matches mode
   - Word pacing matches BSL timing
   - Visual style reflects signing vs gesturing

### ❌ DON'T

1. **Don't display English `text` when in BSL mode**
   ```javascript
   // WRONG: Always shows English
   <div>{{ response.text }}</div>

   // CORRECT: Shows appropriate text
   <div>{{ response.gesture_mode === 'bsl' ? response.bsl_text : response.text }}</div>
   ```

2. **Don't assume `text` and `bsl_text` have same word count**
   ```javascript
   // WRONG: Word counts may differ
   const englishWords = response.text.split(' ');
   const bslWords = response.bsl_text.split(' ');
   // englishWords.length !== bslWords.length
   ```

3. **Don't ignore the transformation**
   ```javascript
   // WRONG: Forces English word order
   const words = response.text.split(' ');

   // CORRECT: Uses appropriate text
   const text = response.gesture_mode === 'bsl'
     ? response.bsl_text
     : response.text;
   const words = text.split(' ');
   ```

## Testing

### Test Cases

```typescript
describe('BSL Integration', () => {
  it('displays bsl_text in BSL mode', () => {
    const response = {
      text: 'What is your name?',
      bsl_text: 'your name What',
      gesture_mode: 'bsl'
    };
    const display = getDisplayText(response);
    expect(display).toBe('your name What');
  });

  it('displays text in gesticulation mode', () => {
    const response = {
      text: 'What is your name?',
      bsl_text: null,
      gesture_mode: 'gesticulation'
    };
    const display = getDisplayText(response);
    expect(display).toBe('What is your name?');
  });

  it('timeline words match display text', () => {
    const response = {
      text: 'What is your name?',
      bsl_text: 'your name What',
      gesture_mode: 'bsl',
      gesture_timeline: [
        { word: 'your', start_ms: 0, end_ms: 420 },
        { word: 'name', start_ms: 444, end_ms: 840 },
        { word: 'What', start_ms: 864, end_ms: 1240 }
      ]
    };
    const displayWords = response.bsl_text.split(' ');
    const timelineWords = response.gesture_timeline.map(e => e.word);
    expect(timelineWords).toEqual(displayWords);
  });
});
```

## API Examples

### Request BSL Mode

```bash
curl -X POST http://localhost:7079/transcribe \
  -F "audio=@recording.wav" \
  -F "gesture_mode=bsl" \
  -F "avatar_mode=office"
```

### Response

```json
{
  "status": "ok",
  "text": "I went to the shop yesterday.",
  "bsl_text": "yesterday I went shop",
  "gesture_mode": "bsl",
  "avatar_mode": "office",
  "gesture_timeline": [
    {"word": "yesterday", "intent": "content", "template": "explain", "start_ms": 0, "end_ms": 580},
    {"word": "I", "intent": "content", "template": "subtle", "start_ms": 604, "end_ms": 720},
    {"word": "went", "intent": "action", "template": "action", "start_ms": 744, "end_ms": 1100},
    {"word": "shop", "intent": "topic", "template": "topic", "start_ms": 1124, "end_ms": 1580}
  ]
}
```

## Summary

- **Two text fields**: `text` (English) and `bsl_text` (BSL)
- **Frontend must choose**: Display `bsl_text` in BSL mode, `text` in gesticulation mode
- **Timeline matches display**: Words in `gesture_timeline` correspond to display text
- **Grammar transformation**: STT service handles BSL word order automatically
- **Synchronization**: Use `gesture_timeline` for word-level timing
- **User experience**: Show both texts when helpful (BSL prominent, English subtitle)

This ensures proper BSL representation while maintaining English accessibility.
