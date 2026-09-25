"""SQLAlchemy stand-ins for the multi-user login mocks.

Shared by conftest.py and test modules. Kept out of conftest.py so tests can
import them without executing conftest as a second module.
"""
import types

from sqlalchemy.sql.elements import BinaryExpression, ColumnElement
from sqlalchemy.sql.expression import literal
from sqlalchemy.sql import operators


# --- MOCK COLUMN CLASSES (The definitive fix is here) ---
class MockColumn(ColumnElement):
    """Mocks a SQLAlchemy column for comparison operations."""

    def __init__(self, name):
        self.name = name
        self.type = types.SimpleNamespace(python_type=str)

    def __eq__(self, other):
        # FIX: Wrap the raw string ('other') using literal() to satisfy SQLAlchemy's internal checks.
        # This solves the AttributeError: 'str' object has no attribute '_propagate_attrs'.
        return BinaryExpression(self, literal(other), operators.eq, type_=self.type)

    def __hash__(self):
        return hash(self.name)


class MockSelectQuery(types.SimpleNamespace):
    """Mocks the select object for the login route."""

    def __init__(self, entities):
        super().__init__()
        self.entities = entities
        self.whereclause = types.SimpleNamespace(right=types.SimpleNamespace(value=None))

    def where(self, condition):
        self.whereclause = condition
        return self
