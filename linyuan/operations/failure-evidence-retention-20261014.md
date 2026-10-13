# Retain negative reports, not merely failure reason strings

Consuming source-reject/production-reject used to DELETE the original Actions
artifact. Keep it instead and bind the terminal failure to its exact run ID,
artifact ID and name. Existing expiry/retention periods are unchanged. This
does not restore already deleted reports or grant any approval.

Failed dispatch records, rejection history, lifetime retry counts and all
quality/rights gates remain intact. A retained negative report is not a success,
stock item, new mother or goal credit. It supports actual failure diagnosis and
future independently validated code-specific repair.
