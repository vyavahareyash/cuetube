# Custom Intake Playlist Over Native Watch Later

The YouTube Data API v3 deprecated system Watch Later and Watch History endpoints in 2016, rendering native queues inaccessible without brittle session scraping that risks API ban or BotGuard blocks. We use a user-managed `Queue-Inbox` custom playlist as the primary ingestion seam via official YouTube Data API v3, supplemented by Google Takeout CSV archives for bulk historical seeding. This guarantees official API compliance, stable OAuth 2.0 scopes, predictable quota usage, and zero session ban risk.
