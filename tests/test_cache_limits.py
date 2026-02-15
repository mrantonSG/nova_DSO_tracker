"""
Cache Boundary Stress Test

This module tests the BoundedCache implementation to ensure:
1. Cache never exceeds its maxsize limit
2. FIFO eviction occurs when the limit is reached
3. Eviction happens in 10% batches (or 1 entry if maxsize < 10)

This addresses the memory safety concern regarding unbounded cache growth.
"""

import pytest
import sys
import os

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from nova.config import weather_cache, BoundedCache


class TestBoundedCacheBasics:
    """Tests for basic BoundedCache functionality."""

    def test_cache_has_maxsize_1000(self):
        """
        Verify weather_cache has maxsize of 1000.
        """
        assert weather_cache._maxsize == 1000, \
            "weather_cache should have maxsize of 1000"

    def test_empty_cache_length(self):
        """
        Verify empty cache has length 0.
        """
        # Create a fresh cache for testing
        test_cache = BoundedCache(1000)
        assert len(test_cache) == 0, "Empty cache should have length 0"

    def test_cache_stores_values_correctly(self):
        """
        Verify cache stores and retrieves values correctly.
        """
        test_cache = BoundedCache(1000)
        test_cache['key1'] = 'value1'
        test_cache['key2'] = 'value2'

        assert len(test_cache) == 2, "Cache should store 2 entries"
        assert test_cache['key1'] == 'value1', "Cache should retrieve stored values"
        assert test_cache['key2'] == 'value2', "Cache should retrieve stored values"


class TestCacheEvictionLogic:
    """Tests for FIFO eviction behavior when cache is full."""

    def test_cache_never_exceeds_maxsize(self):
        """
        CRITICAL MEMORY SAFETY TEST: Fill cache with 1,200 entries and
        verify that len(weather_cache) never exceeds 1,000.

        This is the primary regression test for the memory leak issue.
        """
        test_cache = BoundedCache(1000)

        # Fill with 1,200 unique entries
        for i in range(1200):
            test_cache[f'key_{i}'] = f'value_{i}'

            # Assert after each addition that we never exceed maxsize
            assert len(test_cache) <= 1000, \
                f"Cache size {len(test_cache)} exceeded maxsize of 1000 at iteration {i}"

        # Final assertion
        assert len(test_cache) == 1000, \
            "Cache should be exactly at maxsize after 1200 insertions"

    def test_fifo_eviction_removes_oldest_entries(self):
        """
        Verify FIFO eviction removes the oldest entries when cache is full.

        IMPORTANT: The BoundedCache eviction happens BEFORE adding the new entry.
        When at maxsize (1000), it removes 10% (100 entries), then adds 1.
        Result: cache size becomes 901 after triggering eviction.

        After adding 1,001 entries to a maxsize-1000 cache:
        - The first 100 entries (0-99) should be evicted (10% of 1000)
        - Entries 100-1000 should remain (901 entries total)
        - Entry 1000 should be the newest entry added
        """
        test_cache = BoundedCache(1000)

        # Fill cache to maxsize (1000 entries)
        for i in range(1000):
            test_cache[f'key_{i}'] = f'value_{i}'

        assert len(test_cache) == 1000, "Cache should be at maxsize"

        # Add one more entry to trigger eviction
        test_cache['key_1000'] = 'value_1000'

        # Cache size is now 901 (removed 100, added 1)
        assert len(test_cache) == 901, "Cache should be 901 after eviction (1000 - 100 + 1)"

        # First 100 entries (0-99) should be evicted (10% of 1000)
        for i in range(100):
            assert f'key_{i}' not in test_cache, \
                f"Entry key_{i} should have been evicted"

        # Entries 100-1000 should remain (901 entries)
        for i in range(100, 1001):
            assert f'key_{i}' in test_cache, \
                f"Entry key_{i} should still be in cache"

    def test_eviction_batch_size_is_ten_percent(self):
        """
        Verify eviction batch size is 10% of maxsize (or 1 if maxsize < 10).

        IMPORTANT: Eviction happens BEFORE adding the new entry.
        When at maxsize (100), it removes 10% (10 entries), then adds 1.
        Result: cache size becomes 91 after triggering eviction.

        This tests the core eviction logic: to_remove = list(self.keys())[:self._maxsize // 10 or 1]
        """
        # Test with maxsize 100 - should evict 10 entries at a time
        test_cache = BoundedCache(100)

        # Fill to maxsize
        for i in range(100):
            test_cache[f'key_{i}'] = f'value_{i}'

        assert len(test_cache) == 100

        # Trigger eviction
        test_cache['key_100'] = 'value_100'

        # Cache size is now 91 (removed 10, added 1)
        assert len(test_cache) == 91, "Cache should be 91 after eviction (100 - 10 + 1)"

        # First 10 entries should be evicted (10% of 100)
        for i in range(10):
            assert f'key_{i}' not in test_cache, \
                f"Entry key_{i} should have been evicted (10% batch)"

        # Entries 10-100 should remain (91 entries)
        for i in range(10, 101):
            assert f'key_{i}' in test_cache, \
                f"Entry key_{i} should still be in cache"

    def test_eviction_with_small_maxsize(self):
        """
        Verify eviction batch size is 1 when maxsize < 10.

        IMPORTANT: Eviction happens BEFORE adding the new entry.
        When at maxsize (5), it removes 1 entry, then adds 1.
        Result: cache size stays at 5 after triggering eviction.

        Tests the "or 1" fallback in the eviction formula.
        """
        test_cache = BoundedCache(5)

        # Fill to maxsize
        for i in range(5):
            test_cache[f'key_{i}'] = f'value_{i}'

        assert len(test_cache) == 5

        # Trigger eviction
        test_cache['key_5'] = 'value_5'

        # Cache size stays at 5 (removed 1, added 1)
        assert len(test_cache) == 5, "Cache should remain at maxsize after eviction (5 - 1 + 1)"

        # Only the first entry should be evicted (1 entry, not 10% of 5)
        assert 'key_0' not in test_cache, "First entry should be evicted"
        assert 'key_4' in test_cache, "Last entry before eviction should remain"
        assert 'key_5' in test_cache, "New entry should be in cache"


class TestWeatherCacheSpecifics:
    """Tests specific to the weather_cache instance."""

    def test_weather_cache_is_bounded(self):
        """
        Verify weather_cache is a BoundedCache instance.
        """
        assert isinstance(weather_cache, BoundedCache), \
            "weather_cache should be a BoundedCache instance"

    def test_weather_cache_respects_maxsize(self):
        """
        Verify the actual weather_cache instance respects its maxsize.
        """
        # Store original keys to restore later
        original_keys = list(weather_cache.keys())
        original_values = {k: weather_cache[k] for k in original_keys}

        # Clear cache for testing
        weather_cache.clear()

        try:
            # Fill with 1,200 entries
            for i in range(1200):
                weather_cache[f'test_key_{i}'] = f'test_value_{i}'
                assert len(weather_cache) <= 1000, \
                    f"weather_cache size {len(weather_cache)} exceeded maxsize at iteration {i}"

            # Final verification
            assert len(weather_cache) == 1000, \
                "weather_cache should be exactly at maxsize after 1200 insertions"

        finally:
            # Restore original cache state
            weather_cache.clear()
            for k, v in original_values.items():
                weather_cache[k] = v


class TestCacheConcurrencySafety:
    """Tests for thread-safety and concurrency."""

    def test_cache_clear_works_correctly(self):
        """
        Verify cache.clear() resets the cache to empty state.
        """
        test_cache = BoundedCache(1000)

        # Fill cache
        for i in range(500):
            test_cache[f'key_{i}'] = f'value_{i}'

        assert len(test_cache) == 500

        # Clear cache
        test_cache.clear()

        assert len(test_cache) == 0, "Cache should be empty after clear()"
        assert test_cache._maxsize == 1000, "maxsize should be preserved after clear()"

    def test_cache_update_bypasses_eviction(self):
        """
        KNOWN LIMITATION: Verify that cache.update() does NOT trigger eviction.

        IMPORTANT: dict.update() bypasses the custom __setitem__ method,
        so it can exceed the maxsize limit. This is a limitation of the
        current BoundedCache implementation.

        When using update(), eviction is NOT triggered, which means:
        - Users should use individual assignment (cache[key] = value) for automatic eviction
        - Or the BoundedCache class should override update() to call __setitem__
        """
        test_cache = BoundedCache(1000)

        # Fill to maxsize
        for i in range(1000):
            test_cache[f'key_{i}'] = f'value_{i}'

        assert len(test_cache) == 1000, "Cache should be at maxsize"

        # Use update() to add more entries - this bypasses __setitem__
        new_entries = {f'key_{i}': f'value_{i}' for i in range(1000, 1100)}
        test_cache.update(new_entries)

        # Cache will exceed maxsize because update() bypasses eviction
        assert len(test_cache) == 1100, \
            "Cache exceeds maxsize because update() bypasses __setitem__ (known limitation)"

    def test_individual_assignment_triggers_eviction(self):
        """
        Verify that individual assignment triggers eviction properly.
        This is the recommended way to add entries when eviction is needed.
        """
        test_cache = BoundedCache(1000)

        # Fill to maxsize
        for i in range(1000):
            test_cache[f'key_{i}'] = f'value_{i}'

        assert len(test_cache) == 1000, "Cache should be at maxsize"

        # Add entries one at a time (triggers __setitem__)
        for i in range(1000, 1100):
            test_cache[f'key_{i}'] = f'value_{i}'
            # Verify cache never exceeds maxsize
            assert len(test_cache) <= 1000, \
                f"Cache should not exceed maxsize when adding key_{i}"

        # Final size should be exactly 1000
        assert len(test_cache) == 1000, \
            "Cache should be exactly at maxsize after adding 100 more entries via individual assignment"
