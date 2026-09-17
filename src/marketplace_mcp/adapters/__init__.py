"""One package per marketplace, each speaking to its own site.

An adapter owns its session, its parsing and the shape its site answers in, and
hands the core plain records. Adapters do not import each other.
"""
