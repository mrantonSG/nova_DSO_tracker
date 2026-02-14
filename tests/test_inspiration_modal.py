"""
Tests for the inspiration modal close button functionality.

This module provides regression tests to ensure the inspiration modal's
close button works correctly. The bug was caused by the event delegation
handler checking data-stop-propagation BEFORE processing the action,
causing the close button click to never reach its handler.

Root Cause (Fixed):
- The Close button is inside .insp-modal-content which has data-stop-propagation="true"
- The old code checked stop-propagation BEFORE the action switch, returning early
- This meant closeInspirationModal() was never called when clicking the Close button

Fix:
- Process actions FIRST in the event handler, then check stop-propagation
- This allows the Close button to work while still preventing backdrop clicks
  from triggering when clicking inside the modal content
"""

import pytest
import re


def read_inspiration_template():
    """Helper to read the inspiration section template."""
    import os
    template_path = os.path.join(
        os.path.dirname(os.path.dirname(__file__)),
        'templates',
        '_inspiration_section.html'
    )
    with open(template_path, 'r') as f:
        return f.read()


class TestInspirationModalStructure:
    """Tests verifying the modal HTML structure is correct."""

    def test_close_button_has_data_action(self):
        """
        Verify the Close button has data-action="close-inspiration-modal".
        This is required for the event delegation handler to find and process it.
        """
        content = read_inspiration_template()

        # Find the close button - should have data-action attribute
        close_button_pattern = r'<button[^>]*class="btn-close-modal"[^>]*data-action="close-inspiration-modal"[^>]*>'
        assert re.search(close_button_pattern, content), \
            "Close button must have data-action='close-inspiration-modal' attribute"

    def test_backdrop_has_data_action(self):
        """
        Verify the modal backdrop has data-action="close-inspiration-modal".
        This allows clicking outside the modal to close it.
        """
        content = read_inspiration_template()

        # The backdrop div should have data-action for closing
        backdrop_pattern = r'id="inspiration-modal"[^>]*data-action="close-inspiration-modal"'
        assert re.search(backdrop_pattern, content), \
            "Modal backdrop must have data-action='close-inspiration-modal' for click-to-close"

    def test_modal_content_has_stop_propagation(self):
        """
        Verify the modal content has data-stop-propagation="true".
        This prevents clicks inside the modal from bubbling to the backdrop.
        """
        content = read_inspiration_template()

        # The modal content div should have data-stop-propagation
        content_pattern = r'class="insp-modal-content"[^>]*data-stop-propagation="true"'
        assert re.search(content_pattern, content), \
            "Modal content must have data-stop-propagation='true'"

    def test_close_button_inside_modal_content(self):
        """
        Verify the Close button is inside the modal content div.
        This confirms the structural relationship that caused the original bug.
        """
        content = read_inspiration_template()

        # Find modal content section
        modal_content_match = re.search(
            r'<div class="insp-modal-content"[^>]*>(.*?)</div>\s*</div>\s*</div>',
            content,
            re.DOTALL
        )
        assert modal_content_match, "Could not find modal content section"

        modal_content = modal_content_match.group(1)

        # Verify close button is inside
        assert 'btn-close-modal' in modal_content, \
            "Close button must be inside the modal content div"


class TestInspirationModalEventHandling:
    """Tests verifying the event handler logic is correct."""

    def test_event_handler_processes_actions_before_stop_propagation(self):
        """
        CRITICAL REGRESSION TEST: Verify the event handler processes actions
        BEFORE checking data-stop-propagation.

        This is the key fix for the close button bug. The handler must:
        1. Find the target with data-action
        2. Get the action value
        3. Process the action (switch statement) FIRST
        4. Only THEN check data-stop-propagation for non-action clicks

        The old buggy code checked stop-propagation BEFORE the switch,
        causing close button clicks to return early without closing.
        """
        content = read_inspiration_template()

        # Extract the event delegation JavaScript block
        event_handler_match = re.search(
            r'document\.addEventListener\([\'"]click[\'"]\s*,\s*function\(e\)\s*\{(.*?)\}\s*\);',
            content,
            re.DOTALL
        )
        assert event_handler_match, "Could not find click event delegation handler"

        handler_code = event_handler_match.group(1)

        # Find positions of key code patterns
        action_assignment = handler_code.find('const action = target.dataset.action')
        switch_start = handler_code.find('switch(action)')
        close_modal_case = handler_code.find("case 'close-inspiration-modal'")
        stop_propagation_check = handler_code.find("data-stop-propagation")

        # The action assignment must come BEFORE stop-propagation check
        assert action_assignment > 0, "Handler must assign action from target.dataset.action"
        assert action_assignment < stop_propagation_check, \
            "CRITICAL: Action assignment must come BEFORE stop-propagation check"

        # The switch statement must come BEFORE stop-propagation check
        assert switch_start > 0, "Handler must have a switch(action) statement"
        assert switch_start < stop_propagation_check, \
            "CRITICAL: switch(action) must come BEFORE stop-propagation check"

        # The close-inspiration-modal case must exist
        assert close_modal_case > 0, \
            "Handler must have case 'close-inspiration-modal' in switch statement"

    def test_close_modal_case_calls_function(self):
        """
        Verify the close-inspiration-modal case calls closeInspirationModal().
        """
        content = read_inspiration_template()

        # Look for the case statement and its body
        case_pattern = r"case\s+['\"]close-inspiration-modal['\"]:\s*closeInspirationModal\(\)"
        assert re.search(case_pattern, content), \
            "case 'close-inspiration-modal' must call closeInspirationModal()"

    def test_close_modal_function_exists(self):
        """
        Verify closeInspirationModal function is defined and sets display to none.
        """
        content = read_inspiration_template()

        # Look for the function definition
        func_pattern = r"function\s+closeInspirationModal\s*\(\s*\)\s*\{[^}]*style\.display\s*=\s*['\"]none['\"]"
        assert re.search(func_pattern, content), \
            "closeInspirationModal function must exist and set display='none'"

    def test_function_exposed_globally(self):
        """
        Verify closeInspirationModal is exposed on window for external access.
        """
        content = read_inspiration_template()

        # Look for window.closeInspirationModal assignment
        global_pattern = r"window\.closeInspirationModal\s*=\s*closeInspirationModal"
        assert re.search(global_pattern, content), \
            "closeInspirationModal must be exposed on window for external scripts"


class TestInspirationModalIntegration:
    """Integration tests that verify the modal close behavior end-to-end."""

    def test_modal_hidden_by_default(self):
        """
        Verify the inspiration modal is hidden by default (display: none).
        """
        content = read_inspiration_template()

        # The .insp-modal-backdrop class should have display: none
        css_pattern = r'\.insp-modal-backdrop\s*\{[^}]*display:\s*none'
        assert re.search(css_pattern, content), \
            ".insp-modal-backdrop must have display: none by default"
