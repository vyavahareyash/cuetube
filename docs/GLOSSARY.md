# Glossary

## Architectural Terms (codebase-design)

- **Module**: Unit with an interface and an implementation (scale-agnostic). *Avoid: unit, component, service.*
- **Interface**: Full contract a caller must know to use the module correctly (types, invariants, ordering, errors, config). *Avoid: API, signature.*
- **Implementation**: Internal body of code inside a module.
- **Seam**: Location where a module's interface lives; place to alter behavior without editing call site. *Avoid: boundary.*
- **Adapter**: Concrete implementation satisfying an interface at a seam.
- **Depth**: Ratio of behavioral capability (leverage) to interface complexity. Deep = small interface, deep implementation.
- **Leverage**: Capability obtained per unit of interface learned.
- **Locality**: Concentration of change, bugs, and invariants in one place.

## Domain Terms

- **Intake Playlist**: User-managed YouTube playlist (`Queue-Inbox`) used as primary ingestion seam to bypass Watch Later API deprecation.
- **Video Item**: Normalized entity containing video ID, title, channel, duration, and publication timestamp.
- **Transcript**: Extracted text content and timestamped segments for a video item.
- **Classification / Triage Result**: Structured metadata produced by LLM classification (topic tags, energy level, cognitive demand, priority).
- **Viewing Block**: Scheduled time slot matching video duration and energy level on the target calendar.
- **Schedule Plan**: Calendar-aligned allocation of triaged videos against available free slots.
