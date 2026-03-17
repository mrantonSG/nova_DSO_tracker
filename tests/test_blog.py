"""
Tests for the Blog feature - Nova DSO Tracker

Tests cover:
- Models (BlogPost, BlogImage, BlogComment)
- Markdown rendering filter
- Routes (public access, auth, permissions)
- Image upload/delete helpers
- Comments functionality
"""

import pytest
import sys
import os
import io
from datetime import datetime
from unittest.mock import MagicMock, patch
import tempfile
import shutil

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from nova import (
    DbUser,
    get_db,
)
from nova.models import BlogPost, BlogImage, BlogComment


# ========== MODEL TESTS ==========


def test_blog_post_model_fields(db_session):
    """Test BlogPost model has correct fields."""
    user = db_session.query(DbUser).filter_by(username="guest_user").first()

    post = BlogPost(
        user_id=user.id,
        title="Test Orion Nebula Shot",
        content="# My First Astrophoto\n\nCheck out this **amazing** shot!",
    )
    db_session.add(post)
    db_session.commit()

    assert post.id is not None
    assert post.title == "Test Orion Nebula Shot"
    assert post.content == "# My First Astrophoto\n\nCheck out this **amazing** shot!"
    assert post.user_id == user.id
    assert isinstance(post.created_at, datetime)
    assert isinstance(post.updated_at, datetime)


def test_blog_image_model_fields(db_session):
    """Test BlogImage model has correct fields."""
    user = db_session.query(DbUser).filter_by(username="guest_user").first()

    post = BlogPost(user_id=user.id, title="Post with images", content="Content")
    db_session.add(post)
    db_session.commit()

    image = BlogImage(
        post_id=post.id,
        filename="blog_abc123.jpg",
        thumb_filename="blog_thumb_abc123.jpg",
        caption="M42 at 3 hours exposure",
        display_order=0,
    )
    db_session.add(image)
    db_session.commit()

    assert image.id is not None
    assert image.post_id == post.id
    assert image.filename == "blog_abc123.jpg"
    assert image.thumb_filename == "blog_thumb_abc123.jpg"
    assert image.caption == "M42 at 3 hours exposure"
    assert image.display_order == 0


def test_blog_comment_model_fields(db_session):
    """Test BlogComment model has correct fields."""
    user = db_session.query(DbUser).filter_by(username="guest_user").first()

    post = BlogPost(user_id=user.id, title="Post for comments", content="Content")
    db_session.add(post)
    db_session.commit()

    comment = BlogComment(
        post_id=post.id, user_id=user.id, content="Great shot! What camera did you use?"
    )
    db_session.add(comment)
    db_session.commit()

    assert comment.id is not None
    assert comment.post_id == post.id
    assert comment.user_id == user.id
    assert comment.content == "Great shot! What camera did you use?"
    assert isinstance(comment.created_at, datetime)


def test_blog_post_user_relationship(db_session):
    """Test BlogPost.user relationship works correctly."""
    user = db_session.query(DbUser).filter_by(username="guest_user").first()

    post = BlogPost(user_id=user.id, title="Test", content="Content")
    db_session.add(post)
    db_session.commit()

    # Refresh to load relationships
    db_session.refresh(post)

    assert post.user is not None
    assert post.user.username == "guest_user"


def test_blog_post_images_relationship(db_session):
    """Test BlogPost.images relationship with ordering."""
    user = db_session.query(DbUser).filter_by(username="guest_user").first()

    post = BlogPost(user_id=user.id, title="Multi-image post", content="Content")
    db_session.add(post)
    db_session.commit()

    # Add images in non-sequential order
    img2 = BlogImage(post_id=post.id, filename="img2.jpg", display_order=2)
    img0 = BlogImage(post_id=post.id, filename="img0.jpg", display_order=0)
    img1 = BlogImage(post_id=post.id, filename="img1.jpg", display_order=1)
    db_session.add_all([img2, img0, img1])
    db_session.commit()

    db_session.refresh(post)

    assert len(post.images) == 3
    # Should be ordered by display_order
    assert post.images[0].filename == "img0.jpg"
    assert post.images[1].filename == "img1.jpg"
    assert post.images[2].filename == "img2.jpg"


def test_blog_image_cascade_delete(db_session):
    """Test that deleting a BlogPost cascades to delete its images."""
    user = db_session.query(DbUser).filter_by(username="guest_user").first()

    post = BlogPost(user_id=user.id, title="Post to delete", content="Content")
    db_session.add(post)
    db_session.commit()
    post_id = post.id

    image = BlogImage(post_id=post.id, filename="delete_me.jpg", display_order=0)
    db_session.add(image)
    db_session.commit()
    image_id = image.id

    # Delete the post
    db_session.delete(post)
    db_session.commit()

    # Image should also be deleted
    orphan_image = db_session.query(BlogImage).filter_by(id=image_id).first()
    assert orphan_image is None


def test_blog_comment_cascade_delete(db_session):
    """Test that deleting a BlogPost cascades to delete its comments."""
    user = db_session.query(DbUser).filter_by(username="guest_user").first()

    post = BlogPost(user_id=user.id, title="Post with comment", content="Content")
    db_session.add(post)
    db_session.commit()
    post_id = post.id

    comment = BlogComment(post_id=post.id, user_id=user.id, content="Nice!")
    db_session.add(comment)
    db_session.commit()
    comment_id = comment.id

    # Delete the post
    db_session.delete(post)
    db_session.commit()

    # Comment should also be deleted
    orphan_comment = db_session.query(BlogComment).filter_by(id=comment_id).first()
    assert orphan_comment is None


# ========== MARKDOWN FILTER TESTS ==========


def test_render_markdown_filter_bold():
    """Test markdown filter renders bold text."""
    from nova import render_markdown_filter

    result = render_markdown_filter("**bold text**")
    assert "<strong>bold text</strong>" in str(result)


def test_render_markdown_filter_italic():
    """Test markdown filter renders italic text."""
    from nova import render_markdown_filter

    result = render_markdown_filter("*italic text*")
    assert "<em>italic text</em>" in str(result)


def test_render_markdown_filter_heading():
    """Test markdown filter renders headings."""
    from nova import render_markdown_filter

    result = render_markdown_filter("# Heading 1\n## Heading 2")
    assert "<h1>Heading 1</h1>" in str(result)
    assert "<h2>Heading 2</h2>" in str(result)


def test_render_markdown_filter_code_block():
    """Test markdown filter renders fenced code blocks."""
    from nova import render_markdown_filter

    result = render_markdown_filter("```python\nprint('hello')\n```")
    assert "<code" in str(result)
    assert "print" in str(result)


def test_render_markdown_filter_table():
    """Test markdown filter renders tables."""
    from nova import render_markdown_filter

    table_md = """
| Header 1 | Header 2 |
|----------|----------|
| Cell 1   | Cell 2   |
"""
    result = render_markdown_filter(table_md)
    assert "<table>" in str(result)
    assert "<th>" in str(result)
    assert "<td>" in str(result)


def test_render_markdown_filter_xss_stripped():
    """Test markdown filter strips XSS attempts."""
    from nova import render_markdown_filter

    # Script tags should be stripped
    result = render_markdown_filter("<script>alert('xss')</script>")
    assert "<script>" not in str(result)
    assert "alert" not in str(result)

    # Event handlers should be stripped
    result = render_markdown_filter('<img src="x" onerror="alert(1)">')
    assert "onerror" not in str(result)


def test_render_markdown_filter_empty():
    """Test markdown filter handles empty/None input."""
    from nova import render_markdown_filter

    result = render_markdown_filter("")
    assert str(result) == ""

    result = render_markdown_filter(None)
    assert str(result) == ""


def test_render_markdown_filter_links():
    """Test markdown filter renders links with safe attributes."""
    from nova import render_markdown_filter

    result = render_markdown_filter("[Click here](https://example.com)")
    assert '<a href="https://example.com"' in str(result)
    assert "Click here</a>" in str(result)


# ========== ROUTE TESTS - PUBLIC ACCESS ==========


def test_blog_list_public_access(client_logged_out, db_session):
    """Test that blog list is publicly accessible (no login required)."""
    response = client_logged_out.get("/blog/")
    # Should return 200 (public) or redirect to login if configured differently
    # Based on implementation: public access expected
    assert response.status_code == 200


def test_blog_detail_public_access(client, db_session):
    """Test that blog detail is publicly accessible."""
    user = db_session.query(DbUser).filter_by(username="default").first()

    post = BlogPost(user_id=user.id, title="Public Post", content="Public content")
    db_session.add(post)
    db_session.commit()

    response = client.get(f"/blog/{post.id}")
    assert response.status_code == 200
    assert b"Public Post" in response.data


def test_blog_list_pagination(client, db_session):
    """Test blog list pagination works correctly."""
    user = db_session.query(DbUser).filter_by(username="default").first()

    # Create 15 posts to test pagination (10 per page)
    for i in range(15):
        post = BlogPost(user_id=user.id, title=f"Post {i:02d}", content=f"Content {i}")
        db_session.add(post)
    db_session.commit()

    # First page should have posts
    response = client.get("/blog/")
    assert response.status_code == 200

    # Page 2 should also work
    response = client.get("/blog/?page=2")
    assert response.status_code == 200


# ========== ROUTE TESTS - AUTHENTICATED ==========


def test_blog_create_requires_login(client_logged_out):
    """Test that blog create route requires login."""
    response = client_logged_out.get("/blog/create", follow_redirects=False)
    # Should redirect to login
    assert response.status_code == 302
    assert "/login" in response.location


def test_blog_create_get_returns_form(client):
    """Test GET /blog/create returns the form."""
    response = client.get("/blog/create")
    assert response.status_code == 200
    assert b"form" in response.data.lower()


def test_blog_create_post_valid(client, db_session):
    """Test creating a new blog post with valid data."""
    response = client.post(
        "/blog/create",
        data={
            "title": "My New Astrophoto",
            "content": "# Amazing Shot\n\nCheck out this nebula!",
        },
        follow_redirects=True,
    )

    assert response.status_code == 200

    # Verify post was created
    post = db_session.query(BlogPost).filter_by(title="My New Astrophoto").first()
    assert post is not None
    assert post.content == "# Amazing Shot\n\nCheck out this nebula!"


def test_blog_create_post_missing_title_rejected(client):
    """Test that creating post without title fails."""
    response = client.post(
        "/blog/create",
        data={
            "title": "",
            "content": "Some content",
        },
        follow_redirects=True,
    )

    # Should show error or stay on form
    assert response.status_code == 200
    assert b"required" in response.data.lower() or b"error" in response.data.lower()


def test_blog_create_post_missing_content_rejected(client):
    """Test that creating post without content fails."""
    response = client.post(
        "/blog/create",
        data={
            "title": "A Title",
            "content": "",
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"required" in response.data.lower() or b"error" in response.data.lower()


def test_blog_edit_get_returns_form(client, db_session):
    """Test GET /blog/<id>/edit returns the edit form."""
    user = db_session.query(DbUser).filter_by(username="default").first()

    post = BlogPost(user_id=user.id, title="Edit Me", content="Original content")
    db_session.add(post)
    db_session.commit()

    response = client.get(f"/blog/{post.id}/edit")
    assert response.status_code == 200
    assert b"Edit Me" in response.data


def test_blog_edit_post_updates_content(client, db_session):
    """Test editing a blog post updates the content."""
    user = db_session.query(DbUser).filter_by(username="default").first()

    post = BlogPost(user_id=user.id, title="Original Title", content="Original")
    db_session.add(post)
    db_session.commit()
    post_id = post.id

    response = client.post(
        f"/blog/{post_id}/edit",
        data={
            "title": "Updated Title",
            "content": "Updated content",
        },
        follow_redirects=True,
    )

    assert response.status_code == 200

    # Verify update
    db_session.expire_all()
    updated_post = db_session.query(BlogPost).filter_by(id=post_id).first()
    assert updated_post.title == "Updated Title"
    assert updated_post.content == "Updated content"


def test_blog_delete_removes_post(client, db_session):
    """Test deleting a blog post."""
    user = db_session.query(DbUser).filter_by(username="default").first()

    post = BlogPost(user_id=user.id, title="Delete Me", content="Goodbye")
    db_session.add(post)
    db_session.commit()
    post_id = post.id

    response = client.post(f"/blog/{post_id}/delete", follow_redirects=True)
    assert response.status_code == 200

    # Verify deletion
    deleted_post = db_session.query(BlogPost).filter_by(id=post_id).first()
    assert deleted_post is None


def test_blog_detail_renders_markdown(client, db_session):
    """Test that blog detail renders markdown content."""
    user = db_session.query(DbUser).filter_by(username="default").first()

    post = BlogPost(
        user_id=user.id,
        title="Markdown Test",
        content="**Bold text** and *italic text*",
    )
    db_session.add(post)
    db_session.commit()

    response = client.get(f"/blog/{post.id}")
    assert response.status_code == 200
    # Markdown should be rendered to HTML
    assert b"<strong>Bold text</strong>" in response.data
    assert b"<em>italic text</em>" in response.data


def test_blog_detail_shows_images(client, db_session):
    """Test that blog detail shows attached images."""
    user = db_session.query(DbUser).filter_by(username="default").first()

    post = BlogPost(user_id=user.id, title="With Image", content="Check my image")
    db_session.add(post)
    db_session.commit()

    image = BlogImage(
        post_id=post.id,
        filename="test_image.jpg",
        thumb_filename="test_thumb.jpg",
        caption="My beautiful nebula",
        display_order=0,
    )
    db_session.add(image)
    db_session.commit()

    response = client.get(f"/blog/{post.id}")
    assert response.status_code == 200
    # Should contain image references
    assert b"test_thumb.jpg" in response.data or b"test_image.jpg" in response.data


# ========== COMMENT TESTS ==========


def test_blog_add_comment_requires_login(client_logged_out, db_session):
    """Test that adding a comment requires login."""
    user = db_session.query(DbUser).filter_by(username="guest_user").first()

    post = BlogPost(user_id=user.id, title="Post", content="Content")
    db_session.add(post)
    db_session.commit()

    response = client_logged_out.post(
        f"/blog/{post.id}/comment",
        data={"comment_content": "Great post!"},
        follow_redirects=False,
    )

    # Should redirect to login
    assert response.status_code == 302


def test_blog_add_comment_success(client, db_session):
    """Test adding a comment to a blog post."""
    user = db_session.query(DbUser).filter_by(username="default").first()

    post = BlogPost(user_id=user.id, title="Commentable", content="Content")
    db_session.add(post)
    db_session.commit()

    response = client.post(
        f"/blog/{post.id}/comment",
        data={"comment_content": "This is my comment!"},
        follow_redirects=True,
    )

    assert response.status_code == 200

    # Verify comment was created
    comment = db_session.query(BlogComment).filter_by(post_id=post.id).first()
    assert comment is not None
    assert comment.content == "This is my comment!"


def test_blog_comment_empty_rejected(client, db_session):
    """Test that empty comments are rejected."""
    user = db_session.query(DbUser).filter_by(username="default").first()

    post = BlogPost(user_id=user.id, title="Post", content="Content")
    db_session.add(post)
    db_session.commit()

    response = client.post(
        f"/blog/{post.id}/comment", data={"comment_content": ""}, follow_redirects=True
    )

    assert response.status_code == 200
    # Should show error
    assert b"empty" in response.data.lower() or b"error" in response.data.lower()


def test_blog_delete_own_comment(client, db_session):
    """Test deleting own comment."""
    user = db_session.query(DbUser).filter_by(username="default").first()

    post = BlogPost(user_id=user.id, title="Post", content="Content")
    db_session.add(post)
    db_session.commit()

    comment = BlogComment(post_id=post.id, user_id=user.id, content="My comment")
    db_session.add(comment)
    db_session.commit()
    comment_id = comment.id

    response = client.post(
        f"/blog/{post.id}/comment/{comment_id}/delete", follow_redirects=True
    )

    assert response.status_code == 200

    # Verify deletion
    deleted = db_session.query(BlogComment).filter_by(id=comment_id).first()
    assert deleted is None


def test_blog_detail_shows_comments(client, db_session):
    """Test that blog detail shows comments."""
    user = db_session.query(DbUser).filter_by(username="default").first()

    post = BlogPost(user_id=user.id, title="With Comments", content="Content")
    db_session.add(post)
    db_session.commit()

    comment = BlogComment(
        post_id=post.id, user_id=user.id, content="This is a test comment"
    )
    db_session.add(comment)
    db_session.commit()

    response = client.get(f"/blog/{post.id}")
    assert response.status_code == 200
    assert b"This is a test comment" in response.data


# ========== IMAGE HELPER TESTS ==========


def test_allowed_image_extensions(db_session):
    """Test that allowed_file accepts valid image extensions."""
    from nova.helpers import allowed_file

    assert allowed_file("photo.jpg") is True
    assert allowed_file("photo.jpeg") is True
    assert allowed_file("photo.png") is True
    assert allowed_file("photo.gif") is True
    assert allowed_file("photo.FITS") is True  # Astro format


def test_disallowed_extension_rejected(db_session):
    """Test that disallowed extensions are rejected."""
    from nova.helpers import allowed_file

    assert allowed_file("malware.exe") is False
    assert allowed_file("script.js") is False
    assert allowed_file("config.php") is False
    assert allowed_file("noextension") is False


# ========== IMAGE ROUTE TESTS ==========


def test_blog_image_route_404_missing(client):
    """Test that missing blog image returns 404."""
    response = client.get("/blog/uploads/1/nonexistent.jpg")
    assert response.status_code == 404


def test_blog_image_route_blocks_path_traversal(client):
    """Test that path traversal attempts are blocked."""
    response = client.get("/blog/uploads/1/../../../etc/passwd")
    assert response.status_code == 404


# ========== AJAX DELETE IMAGE TESTS ==========


def test_blog_delete_image_ajax(client, db_session):
    """Test AJAX image deletion endpoint."""
    user = db_session.query(DbUser).filter_by(username="default").first()

    post = BlogPost(user_id=user.id, title="With Image", content="Content")
    db_session.add(post)
    db_session.commit()

    image = BlogImage(
        post_id=post.id,
        filename="to_delete.jpg",
        thumb_filename="to_delete_thumb.jpg",
        display_order=0,
    )
    db_session.add(image)
    db_session.commit()
    image_id = image.id

    response = client.post(f"/blog/{post.id}/delete-image/{image_id}")

    assert response.status_code == 200
    data = response.get_json()
    assert data.get("success") is True

    # Verify deletion
    deleted = db_session.query(BlogImage).filter_by(id=image_id).first()
    assert deleted is None


def test_blog_delete_image_wrong_post_404(client, db_session):
    """Test that deleting image from wrong post returns 404."""
    user = db_session.query(DbUser).filter_by(username="default").first()

    post1 = BlogPost(user_id=user.id, title="Post 1", content="Content")
    post2 = BlogPost(user_id=user.id, title="Post 2", content="Content")
    db_session.add_all([post1, post2])
    db_session.commit()

    image = BlogImage(post_id=post1.id, filename="img.jpg", display_order=0)
    db_session.add(image)
    db_session.commit()

    # Try to delete image using wrong post ID
    response = client.post(f"/blog/{post2.id}/delete-image/{image.id}")
    assert response.status_code == 404
