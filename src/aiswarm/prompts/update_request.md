# Post-run: update

$packet

## Your journal entry from before the reviews

$first_journal

## Reviews of you, with your responses

$reviews

Now write:
- journal: a second private journal entry, written after reading your reviews. Only you will ever read it ($journal_max characters at most).
- lessons: your lessons.md, replacing the current one ($private_max characters at most).

You may also rewrite any of the following; use null for any you keep as it is.
- self_description: your self.md, private ($private_max characters at most).
- temperament: your temperament.md, private ($private_max characters at most).
- peer_notes: your peer-notes.md, private ($private_max characters at most).
- public_profile: {"name": "...", "headline": "...", "introduction": "..."}, which every member sees: name ($name_max characters at most), headline (a single line, $headline_max characters at most), introduction ($intro_max characters at most).

Reply with JSON only: {"journal": "...", "lessons": "...", "self_description": "..." or null, "temperament": "..." or null, "peer_notes": "..." or null, "public_profile": {...} or null}
