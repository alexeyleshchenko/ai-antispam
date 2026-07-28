"""Tests for log formatting helpers."""

from app.common.utils import format_chat_log, format_user_log


class TestFormatChatLog:
    def test_bare_id(self):
        assert format_chat_log(-100123) == "-100123"

    def test_with_title(self):
        assert format_chat_log(-100123, title="My Group") == "-100123 ('My Group')"

    def test_with_username(self):
        assert format_chat_log(-100123, username="mygroup") == "-100123 (@mygroup)"

    def test_with_title_and_username(self):
        assert (
            format_chat_log(-100123, title="My Group", username="mygroup")
            == "-100123 ('My Group' @mygroup)"
        )

    def test_positive_id(self):
        assert format_chat_log(12345) == "12345"

    def test_none_title_and_username(self):
        assert format_chat_log(-100123, title=None, username=None) == "-100123"


class TestFormatUserLog:
    def test_bare_id(self):
        assert format_user_log(123) == "123"

    def test_with_name(self):
        assert format_user_log(123, name="John") == "123 ('John')"

    def test_with_username(self):
        assert format_user_log(123, username="john") == "123 (@john)"

    def test_with_name_and_username(self):
        assert (
            format_user_log(123, name="John", username="john")
            == "123 ('John' @john)"
        )

    def test_negative_id(self):
        assert format_user_log(-100123) == "-100123"

    def test_none_name_and_username(self):
        assert format_user_log(123, name=None, username=None) == "123"
