# Post-run: review of $heading

Run $run_id. Goal: $goal

## Board entries by $authors

$transcript

$instruction

- more_of: What did $subject do that you'd want more of?
- less_of: What would you want less of?
- private_note: What would you tell $object privately? $private_reader
- evidence: the ids of the board entries in this run that your review draws on (at least one).
- overall: an integer from 1 to 5 answering "Would you want to work with $object again?"
- overall_reason: why.
$dimension_block
$visibility

Reply with JSON only: {"more_of": "...", "less_of": "...", "private_note": "...", "evidence": [<entry ids>], "overall": <1-5>, "overall_reason": "..."$dimension_json}
