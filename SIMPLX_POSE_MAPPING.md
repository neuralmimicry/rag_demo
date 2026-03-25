# SMPL-X / Simplx Avatar Pose Mapping

## Overview

This document defines the coordinate mapping between the STT service's `avatar_motion` pose data and the frontend's Simplx (SMPL-X) avatar skeletal structure.

## Current Pose Structure

### STT Service Output (Rust)

The STT service generates poses with normalized values:

```rust
struct Pose {
    // Head & Neck
    headYaw: f32,         // -1.0 to 1.0 (signed)
    headPitch: f32,       // -1.0 to 1.0 (signed)

    // Spine & Torso
    spineLean: f32,       // -1.0 to 1.0 (signed)
    shoulderRise: f32,    // -1.0 to 1.0 (signed)

    // Arms - Shoulders
    leftShoulderPitch: f32,   // -1.0 to 1.0 (signed)
    rightShoulderPitch: f32,  // -1.0 to 1.0 (signed)
    leftShoulderRoll: f32,    // -1.0 to 1.0 (signed)
    rightShoulderRoll: f32,   // -1.0 to 1.0 (signed)

    // Arms - Elbows
    leftElbow: f32,      // 0.0 to 1.0 (unsigned)
    rightElbow: f32,     // 0.0 to 1.0 (unsigned)

    // Arms - Wrists
    leftWristYaw: f32,   // -1.0 to 1.0 (signed)
    rightWristYaw: f32,  // -1.0 to 1.0 (signed)

    // Legs - Hips
    leftHip: f32,        // -1.0 to 1.0 (signed)
    rightHip: f32,       // -1.0 to 1.0 (signed)

    // Legs - Knees
    leftKnee: f32,       // 0.0 to 1.0 (unsigned)
    rightKnee: f32,      // 0.0 to 1.0 (unsigned)

    // Legs - Ankles
    leftAnkle: f32,      // -1.0 to 1.0 (signed)
    rightAnkle: f32,     // -1.0 to 1.0 (signed)

    // Hands
    leftHand: HandPose,
    rightHand: HandPose,
}

struct HandPose {
    thumb: f32,   // 0.0 to 1.0 (curl amount)
    index: f32,   // 0.0 to 1.0
    middle: f32,  // 0.0 to 1.0
    ring: f32,    // 0.0 to 1.0
    pinky: f32,   // 0.0 to 1.0
}
```

### Frontend Simplx Avatar (React/Three.js)

The frontend currently maps these values to the SMPL-X skeleton:

```javascript
// From ChatOfficeEnvironment.jsx lines 794-890

// Head & Neck
const poseSpineX = -0.08 + toSignedUnit(avatarPose?.spineLean, 0) * 0.38;
const poseSpineY = toSignedUnit(avatarPose?.headYaw, 0) * 0.08;
const poseSpineZ = toSignedUnit(avatarPose?.shoulderRise, 0) * 0.24;
const poseNeckX = 0.05 + toSignedUnit(avatarPose?.headPitch, 0) * 0.28;
const poseNeckY = toSignedUnit(avatarPose?.headYaw, 0) * 0.18;
const poseHeadX = -0.04 + toSignedUnit(avatarPose?.headPitch, 0) * 0.44;
const poseHeadY = toSignedUnit(avatarPose?.headYaw, 0) * 0.34;

// Arms - Shoulders
const poseLeftUpperX = -0.2 + toSignedUnit(avatarPose?.leftShoulderPitch, 0) * 0.82;
const poseRightUpperX = -0.2 + toSignedUnit(avatarPose?.rightShoulderPitch, 0) * 0.82;
const poseLeftUpperZ = -1.25 + toSignedUnit(avatarPose?.leftShoulderRoll, -0.05) * 0.78;
const poseRightUpperZ = 1.25 + toSignedUnit(avatarPose?.rightShoulderRoll, 0.05) * 0.78;

// Arms - Elbows
const poseLeftForeX = -0.1 - toUnsignedUnit(avatarPose?.leftElbow, 0.28) * 1.02;
const poseRightForeX = -0.1 - toUnsignedUnit(avatarPose?.rightElbow, 0.28) * 1.02;

// Arms - Wrists
const poseLeftHandX = -0.06 - toSignedUnit(avatarPose?.leftWristYaw, 0) * 0.2;
const poseRightHandX = -0.06 + toSignedUnit(avatarPose?.rightWristYaw, 0) * 0.2;
const poseLeftHandZ = -0.06 + toSignedUnit(avatarPose?.leftWristYaw, 0) * 0.24;
const poseRightHandZ = 0.06 + toSignedUnit(avatarPose?.rightWristYaw, 0) * 0.24;

// Hands (finger curl)
const leftPoseCurl = averageHandCurl(avatarPose?.leftHand);
const rightPoseCurl = averageHandCurl(avatarPose?.rightHand);
```

## Coordinate System

### Axes Convention
- **X-axis**: Forward/Back rotation (pitch)
- **Y-axis**: Left/Right rotation (yaw)
- **Z-axis**: Roll/Twist rotation

### Value Ranges
- **Signed (-1.0 to 1.0)**: Bidirectional rotation (head yaw, wrist rotation, etc.)
- **Unsigned (0.0 to 1.0)**: Unidirectional flex (elbow bend, knee bend, finger curl)

## Simplx Bone Hierarchy

```
Pelvis (root)
├── Spine1
│   ├── Spine2
│   │   ├── Spine3
│   │   │   ├── Neck
│   │   │   │   └── Head
│   │   │   │       └── Jaw
│   │   │   ├── Left_Shoulder
│   │   │   │   ├── Left_Elbow
│   │   │   │   │   ├── Left_Wrist
│   │   │   │   │   │   └── Left_Hand
│   │   │   │   │   │       ├── Thumb (joints)
│   │   │   │   │   │       ├── Index (joints)
│   │   │   │   │   │       ├── Middle (joints)
│   │   │   │   │   │       ├── Ring (joints)
│   │   │   │   │   │       └── Pinky (joints)
│   │   │   └── Right_Shoulder
│   │   │       └── (mirror of left arm)
│   ├── Left_Hip
│   │   ├── Left_Knee
│   │   │   └── Left_Ankle
│   └── Right_Hip
│       └── (mirror of left leg)
```

## Bone Normalization

The frontend uses canonical bone name matching (from `SmplXViewer.jsx`):

```javascript
const CANON = {
    pelvis: ["pelvis", "hips", "hip", "root"],
    spine1: ["spine1", "spine_01", "spineupper", "spine"],
    spine2: ["spine2", "spine_02", "spinemid"],
    spine3: ["spine3", "spine_03", "spinelower"],
    neck: ["neck", "neck_01"],
    head: ["head", "head_01"],
    jaw: ["jaw", "jaw_01"],

    left_shoulder: ["left_shoulder", "l_shoulder", "upperarm_l", ...],
    left_elbow: ["left_elbow", "l_elbow", "lowerarm_l", ...],
    left_wrist: ["left_wrist", "l_wrist"],
    left_hand: ["hand_l", "left_hand", "l_hand", ...],

    right_shoulder: ["right_shoulder", "r_shoulder", ...],
    // ... etc
};
```

## Current Issues & Recommendations

### ✅ Already Correct

1. **Pose Structure**: STT output matches frontend expectations
2. **Value Normalization**: Signed/unsigned ranges are properly implemented
3. **Hand Poses**: Finger curl values are correctly averaged
4. **Bone Matching**: Frontend has flexible canonical name matching

### ❌ Missing: Text/Motion Independence

**Problem**: User cannot independently control:
- Text display mode (English vs BSL)
- Motion type (gesticulation vs BSL signing)

**Current Behavior**:
```javascript
// Line 627-628
const [gestureMode, setGestureMode] = useState('gesticulation');
const [activeGestureMode, setActiveGestureMode] = useState('gesticulation');
```

This single `gestureMode` state controls BOTH text and motion.

### ❌ Missing: BSL Text Display

**Problem**: Frontend doesn't use `bsl_text` field from STT response.

**Current Code** (sttTextUtils.js line 67-138):
```javascript
export const extractTranscriptFromPayload = (payload) => {
    // ... walks through payload looking for 'text', 'transcript', etc.
    // BUT never checks for 'bsl_text'
}
```

### ❌ Missing: Coordinate Validation

No validation that STT-generated coordinates are within valid ranges for the Simplx skeleton.

## Required Changes

### 1. Add Independent Text/Motion Controls

```javascript
// AIChatWidget.jsx additions
const [textDisplayMode, setTextDisplayMode] = useState('english'); // 'english' | 'bsl'
const [motionMode, setMotionMode] = useState('gesticulation'); // 'gesticulation' | 'bsl'

// UI Controls
const TEXT_DISPLAY_OPTIONS = [
    { value: 'english', label: 'English' },
    { value: 'bsl', label: 'BSL Grammar' },
];

const MOTION_MODE_OPTIONS = [
    { value: 'gesticulation', label: 'Gesticulation' },
    { value: 'bsl', label: 'BSL Signing' },
];
```

### 2. Update Text Extraction to Support BSL

```javascript
// sttTextUtils.js enhancement
export const extractTranscriptFromPayload = (payload, preferBsl = false) => {
    if (!payload || typeof payload !== 'object') return '';

    // If BSL text is preferred and available, use it
    if (preferBsl && payload.bsl_text) {
        return normalizeTranscriptText(payload.bsl_text);
    }

    // Otherwise use English text
    const directKeys = ['text', 'transcript', 'transcription', 'output_text', 'utterance'];
    for (const key of directKeys) {
        const directText = normalizeTranscriptText(payload[key]);
        if (directText) return directText;
    }

    // ... existing fallback logic
};

export const extractBslTextFromPayload = (payload) => {
    if (!payload || typeof payload !== 'object') return null;
    return normalizeTranscriptText(payload.bsl_text) || null;
};
```

### 3. Update STT Request Parameters

```javascript
// When calling STT API, pass motion mode (not text mode)
const transcribeAudio = async (audioBlob, options = {}) => {
    const formData = new FormData();
    formData.append('audio', audioBlob);
    formData.append('lang', options.lang || 'en-GB');
    formData.append('gesture_mode', options.motionMode || 'gesticulation'); // ← motion mode
    formData.append('avatar_mode', options.avatarMode || 'chat');
    // ... rest of request
};
```

### 4. Display Appropriate Text

```javascript
// In message rendering
const displayText = textDisplayMode === 'bsl'
    ? message.bslText || message.text  // BSL text if available, else English
    : message.text;                     // Always English

// Optionally show both
{textDisplayMode === 'bsl' && message.bslText && (
    <div className="bsl-primary">{message.bslText}</div>
)}
{textDisplayMode === 'bsl' && message.bslText && message.text && (
    <div className="english-subtitle">{message.text}</div>
)}
{textDisplayMode === 'english' && (
    <div>{message.text}</div>
)}
```

### 5. Add Coordinate Validation

```javascript
// Add to sttTextUtils.js or new file
export const validatePoseCoordinates = (pose) => {
    if (!pose || typeof pose !== 'object') return false;

    const checkSigned = (val, name) => {
        const num = Number(val);
        if (!Number.isFinite(num) || num < -1.0 || num > 1.0) {
            console.warn(`Invalid signed coordinate ${name}: ${val}`);
            return false;
        }
        return true;
    };

    const checkUnsigned = (val, name) => {
        const num = Number(val);
        if (!Number.isFinite(num) || num < 0.0 || num > 1.0) {
            console.warn(`Invalid unsigned coordinate ${name}: ${val}`);
            return false;
        }
        return true;
    };

    // Validate all signed coordinates
    const signedFields = ['headYaw', 'headPitch', 'spineLean', 'shoulderRise',
        'leftShoulderPitch', 'rightShoulderPitch', 'leftShoulderRoll', 'rightShoulderRoll',
        'leftWristYaw', 'rightWristYaw', 'leftHip', 'rightHip', 'leftAnkle', 'rightAnkle'];

    for (const field of signedFields) {
        if (pose[field] !== undefined && !checkSigned(pose[field], field)) {
            return false;
        }
    }

    // Validate all unsigned coordinates
    const unsignedFields = ['leftElbow', 'rightElbow', 'leftKnee', 'rightKnee'];

    for (const field of unsignedFields) {
        if (pose[field] !== undefined && !checkUnsigned(pose[field], field)) {
            return false;
        }
    }

    // Validate hand poses
    const handFields = ['thumb', 'index', 'middle', 'ring', 'pinky'];
    if (pose.leftHand) {
        for (const finger of handFields) {
            if (pose.leftHand[finger] !== undefined && !checkUnsigned(pose.leftHand[finger], `leftHand.${finger}`)) {
                return false;
            }
        }
    }
    if (pose.rightHand) {
        for (const finger of handFields) {
            if (pose.rightHand[finger] !== undefined && !checkUnsigned(pose.rightHand[finger], `rightHand.${finger}`)) {
                return false;
            }
        }
    }

    return true;
};
```

## Implementation Checklist

### Frontend Changes Required

- [ ] Add `textDisplayMode` state (english/bsl)
- [ ] Add `motionMode` state (gesticulation/bsl)
- [ ] Add UI controls for both modes
- [ ] Update `extractTranscriptFromPayload` to support BSL text
- [ ] Add `extractBslTextFromPayload` function
- [ ] Store both `text` and `bslText` in message objects
- [ ] Display appropriate text based on `textDisplayMode`
- [ ] Pass `motionMode` to STT API (not text mode)
- [ ] Add coordinate validation
- [ ] Update settings persistence to save both modes
- [ ] Add mode indicators in UI

### Documentation Updates

- [ ] Update user guide with new controls
- [ ] Document BSL text vs motion independence
- [ ] Add coordinate validation documentation
- [ ] Update API integration examples

## Testing Scenarios

1. **English Text + Gesticulation Motion**
   - User selects: Text=English, Motion=Gesticulation
   - Expected: English words displayed, natural gestures

2. **BSL Text + BSL Motion**
   - User selects: Text=BSL, Motion=BSL
   - Expected: BSL grammar displayed, signing motions

3. **English Text + BSL Motion** (Advanced)
   - User selects: Text=English, Motion=BSL
   - Expected: English words, but BSL signing motions
   - Use case: Teaching BSL to English speakers

4. **BSL Text + Gesticulation Motion** (Advanced)
   - User selects: Text=BSL, Motion=Gesticulation
   - Expected: BSL grammar, natural gestures
   - Use case: Learning BSL grammar without full signing

## Summary

The STT service and Simplx avatar structure are correctly aligned. The main missing pieces are:

1. **User controls** for independent text/motion selection
2. **BSL text extraction** from STT responses
3. **Display logic** to show appropriate text based on user preference
4. **Coordinate validation** for robustness

All changes are frontend-only; no STT service changes needed.
