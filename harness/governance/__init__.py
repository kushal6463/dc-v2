"""Governance authoring — Policy & Threshold extraction from documents.

The LLM document-parser (:mod:`harness.governance.extract`) reads pasted/uploaded
text and returns draft ``{policy, threshold}`` fields to prefill the canvas
authoring wizard. It is driven from its own OS process
(:mod:`harness.governance.extract_subprocess`) because the agent SDK's bundled
``claude`` only completes its stdin handshake from a clean main-thread
``asyncio.run`` — the same constraint that keeps ingestion in a subprocess.
"""
