# URI Scheme Refactoring Summary

**Date**: January 2025
**Status**: ✅ Complete

---

## Changes Made

Successfully refactored the MCP server from a scheme-based URI format to a RESTful path-based format.

### Old URI Scheme (v1.0 Initial)
```
podcast://1
podcast://https%3A%2F%2Ffeeds.example.com%2Frss

episode://1/latest
episode://1/3
episode://1/2025-01-15

transcript://1/latest
transcript://1/2
```

### New URI Scheme (v1.0 Final - RESTful)
```
thestill://podcasts/1
thestill://podcasts/https%3A%2F%2Ffeeds.example.com%2Frss

thestill://podcasts/1/episodes/latest
thestill://podcasts/1/episodes/3
thestill://podcasts/1/episodes/2025-01-15

thestill://podcasts/1/episodes/latest/transcript
thestill://podcasts/1/episodes/2/transcript

thestill://podcasts/1/episodes/1/audio  # NEW!
```

---

## Why We Changed

### Rationale
1. **Better Resource Hierarchy** - Clear parent-child relationships
2. **RESTful Convention** - Familiar to developers, follows web standards
3. **Single Authority** - All resources under `thestill://` namespace
4. **Easier Extension** - Simple to add new resource types
5. **Audio Resource Separation** - Proper `/audio` sub-resource

### Benefits
- ✅ Self-documenting URI structure
- ✅ Mirrors REST API patterns
- ✅ More explicit resource relationships
- ✅ Easier to extend (e.g., `/speakers`, `/summary`)
- ✅ Better for future features

### Trade-offs Accepted
- ⚠️ Slightly longer URIs (acceptable for clarity)
- ⚠️ 2-3 hours of refactoring (worth the improvement)

---

## Files Modified

### Code Changes

**`thestill/mcp/utils.py`** - Complete rewrite
- Replaced `parse_podcast_uri()`, `parse_episode_uri()`, `parse_transcript_uri()`
- New: `parse_thestill_uri()` - Unified parser for RESTful paths
- New: `build_podcast_uri()`, `build_episode_uri()`, `build_transcript_uri()`, `build_audio_uri()` - URI builders
- New: `_parse_id()` - Helper for ID parsing

**`thestill/mcp/resources.py`** - Major refactoring
- Updated imports to use new URI parser
- Refactored `read_resource()` to use unified parsing
- Added support for `audio` resource type
- Updated `list_resources()` with 4 resource types (added audio)

### Documentation Changes

**`docs/MCP_USAGE.md`** - Comprehensive update
- All URI examples updated to RESTful format
- Added "Episode Audio Reference" section
- Updated all usage examples throughout
- Fixed cross-references

---

## URI Structure

### Format Specification

```
thestill://podcasts/{podcast_id}
   └─ Podcast metadata (JSON)

thestill://podcasts/{podcast_id}/episodes/{episode_id}
   └─ Episode metadata (JSON)

thestill://podcasts/{podcast_id}/episodes/{episode_id}/transcript
   └─ Cleaned transcript (Markdown)

thestill://podcasts/{podcast_id}/episodes/{episode_id}/audio
   └─ Audio reference (JSON)
```

### ID Resolution

**Podcast ID**:
- Integer: `1`, `2`, `3` → podcast index
- String: `https://...` → RSS URL

**Episode ID**:
- Integer: `1`, `2`, `3` → episode index (1=latest)
- Keyword: `latest` → most recent episode
- Date: `2025-01-15` → episode by publish date
- String: GUID → exact match

### Examples

```
# Get podcast by index
thestill://podcasts/1

# Get podcast by URL
thestill://podcasts/https%3A%2F%2Ffeeds.example.com%2Frss

# Get latest episode
thestill://podcasts/1/episodes/latest

# Get 3rd latest episode
thestill://podcasts/1/episodes/3

# Get episode by date
thestill://podcasts/1/episodes/2025-01-15

# Get episode by GUID
thestill://podcasts/1/episodes/abc123-def456

# Get transcript
thestill://podcasts/1/episodes/latest/transcript

# Get audio reference
thestill://podcasts/1/episodes/1/audio
```

---

## Implementation Details

### Parsing Logic

The new `parse_thestill_uri()` function:

1. Validates `thestill://` scheme
2. Splits path into segments
3. Validates `podcasts` namespace (first segment)
4. Parses podcast ID (second segment)
5. If more segments:
   - Validates `episodes` namespace (third segment)
   - Parses episode ID (fourth segment)
6. If five segments:
   - Validates sub-resource (`transcript` or `audio`)

Returns:
```python
{
    "resource": "podcast" | "episode" | "transcript" | "audio",
    "podcast_id": int | str,
    "episode_id": int | str  # optional
}
```

### Error Handling

Comprehensive validation with clear error messages:

```python
# Invalid scheme
"Invalid URI scheme: http://... Expected thestill://"

# Missing segments
"Invalid URI format: ... Expected thestill://podcasts/{id}/..."

# Invalid namespace
"Invalid URI: ... Expected 'podcasts' as first path segment"

# Invalid sub-resource
"Invalid sub-resource: summary. Expected 'transcript' or 'audio'"
```

---

## Testing Checklist

- [x] `parse_thestill_uri()` handles all valid formats
- [x] Error handling for invalid URIs
- [x] `build_*_uri()` helpers create valid URIs
- [x] `read_resource()` works with new parsing
- [x] All resource types accessible (podcast, episode, transcript, audio)
- [x] Documentation updated with examples
- [ ] Integration test with Claude Desktop (pending)

---

## Future Extensions

The RESTful structure makes it easy to add:

### v1.1+
```
# Speaker information
thestill://podcasts/1/episodes/1/speakers

# Episode summary
thestill://podcasts/1/episodes/1/summary

# Podcast statistics
thestill://podcasts/1/statistics

# Search functionality
thestill://search?q=keyword&type=transcript

# Playlist support
thestill://playlists/favorites
```

### v2.0+
```
# User collections
thestill://collections/reading-list

# Annotations
thestill://podcasts/1/episodes/1/annotations

# Clips
thestill://podcasts/1/episodes/1/clips/123
```

---

## Migration Notes

### For Users
- **No action required** - This is an internal implementation change
- URIs were not publicly documented before completion
- Claude Desktop integration uses the new format from day one

### For Developers
If you were testing with the old format:

1. **Update any test scripts**:
   - Replace `podcast://` with `thestill://podcasts/`
   - Replace `episode://` with `thestill://podcasts/{id}/episodes/`
   - Replace `transcript://` with `thestill://podcasts/{id}/episodes/{id}/transcript`

2. **No API changes** - Tools and resources work the same
3. **Better clarity** - New URIs are self-explanatory

---

## Comparison Table

| Aspect | Old Scheme | New Scheme (RESTful) |
|--------|-----------|---------------------|
| **Authority** | Multiple (`podcast://`, `episode://`) | Single (`thestill://`) |
| **Hierarchy** | Flat with `:// separators` | Nested paths |
| **Podcast** | `podcast://1` | `thestill://podcasts/1` |
| **Episode** | `episode://1/latest` | `thestill://podcasts/1/episodes/latest` |
| **Transcript** | `transcript://1/latest` | `thestill://podcasts/1/episodes/latest/transcript` |
| **Audio** | N/A (not separated) | `thestill://podcasts/1/episodes/1/audio` |
| **Extensibility** | Limited | Excellent |
| **Readability** | Good | Excellent |
| **Web Standard** | No | Yes (REST-like) |

---

## Performance Impact

**No performance impact**:
- Parsing complexity: O(1) - fixed number of path segments
- String operations: Negligible (<1ms)
- Backward compatibility: N/A (not released yet)

---

## Conclusion

The RESTful URI scheme provides:
- ✅ Better developer experience
- ✅ Clearer resource hierarchy
- ✅ Easier future extensions
- ✅ Industry-standard patterns
- ✅ Separated audio resource

**Decision**: Correct choice to refactor before release. The improved architecture will serve the project well as it grows.

---

**Completed by**: Claude Code
**Approved by**: User
**Implementation Time**: ~2 hours
**Status**: Ready for testing
