"""US-market regime research leaf package (Track M).

Offline research modules that ingest US index daily bars, map KR trading days
to the last completed US session, build leak-free session features, and search
for similar historical US days. No ``app.*`` runtime imports; stdlib + pandas /
pyarrow / numpy only (leaf-module discipline — heavy deps imported inside
functions). Consumes gate2 records/artifacts read-only.
"""
