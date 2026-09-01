"""Control plane tests.

A package rather than a loose directory so that shared helpers in conftest can
be imported explicitly. An implicit fixture would work for values; `prose` is a
pure function used inside assertions, and importing it says where it came from.
"""
