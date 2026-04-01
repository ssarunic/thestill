---
name: code-review
description: Comprehensive code review and peer review validation. Use when asked to review code, perform code review, validate code quality, or when evaluating peer review findings. Triggers on requests like "review this code", "code review", "check this PR", "peer review", or when asked to validate feedback from another reviewer.
---

# Code Review Skill

Two review workflows: comprehensive code review and peer review validation.

## Comprehensive Review (`/code-review`)

Perform thorough but concise code review.

### Checklist

- **Logging** - No console.log; proper logger with context
- **Error Handling** - Try-catch for async, centralized handlers, helpful messages
- **TypeScript** - No `any` types, proper interfaces, no @ts-ignore
- **Production Readiness** - No debug statements, TODOs, or hardcoded secrets
- **React/Hooks** - Effects have cleanup, dependencies complete, no infinite loops
- **Performance** - No unnecessary re-renders, expensive calcs memoized
- **Security** - Auth checked, inputs validated, RLS policies in place
- **Architecture** - Follows existing patterns, code in correct directory

### Output Format

```
### ✅ Looks Good
- [Item 1]
- [Item 2]

### ⚠️ Issues Found
- **[Severity]** [[File:line](File:line)] - [Issue description]
  - Fix: [Suggested fix]

### 📊 Summary
- Files reviewed: X
- Critical issues: X
- Warnings: X
```

### Severity Levels

- **CRITICAL** - Security, data loss, crashes
- **HIGH** - Bugs, performance issues, bad UX
- **MEDIUM** - Code quality, maintainability
- **LOW** - Style, minor improvements

## Peer Review Validation (`/peer-review`)

Validate findings from another reviewer who has less project context.

### For Each Finding

1. **Verify it exists** - Actually check the code. Does this issue really exist?
2. **If it doesn't exist** - Explain why (already handled, misunderstood architecture)
3. **If it does exist** - Assess severity and add to fix plan

### Output Format

```
### ✅ Valid Findings
- [Finding] - Confirmed, [severity assessment]

### ❌ Invalid Findings
- [Finding] - Why this is incorrect or already handled

### 🔧 Action Plan
1. [Prioritized fix item]
2. [Prioritized fix item]
```

Don't accept findings at face value - critically evaluate each one against actual code.
