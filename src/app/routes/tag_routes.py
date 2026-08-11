"""CRUD API for reusable prompt tags."""

from __future__ import annotations

from typing import Any

from flask import Blueprint, jsonify, request
from flask.typing import ResponseReturnValue

from app.auth.guards import require_admin
from app.extensions import db
from app.models import Tag
from app.writer.client import writer_client

tag_bp = Blueprint("tag", __name__)


def _serialize_tag(tag: Tag) -> dict[str, Any]:
    return {
        "id": tag.id,
        "name": tag.name,
        "prompt": tag.prompt,
        "created_at": tag.created_at.isoformat() if tag.created_at else None,
        "updated_at": tag.updated_at.isoformat() if tag.updated_at else None,
    }


@tag_bp.route("/api/tags", methods=["GET"])
def list_tags() -> ResponseReturnValue:
    _, error_response = require_admin("list prompt tags")
    if error_response is not None:
        return error_response

    tags = Tag.query.order_by(Tag.name.asc()).all()
    return jsonify([_serialize_tag(tag) for tag in tags])


@tag_bp.route("/api/tags", methods=["POST"])
def create_tag() -> ResponseReturnValue:
    _, error_response = require_admin("create prompt tags")
    if error_response is not None:
        return error_response

    payload = request.get_json(silent=True) or {}
    name = payload.get("name")
    prompt = payload.get("prompt")

    if not isinstance(name, str) or not name.strip():
        return jsonify({"error": "name is required"}), 400
    if prompt is not None and not isinstance(prompt, str):
        return jsonify({"error": "prompt must be a string or null"}), 400

    trimmed_name = name.strip()
    if Tag.query.filter_by(name=trimmed_name).first() is not None:
        return jsonify({"error": "A tag with that name already exists"}), 409

    result = writer_client.create(
        "Tag",
        {
            "name": trimmed_name,
            "prompt": prompt.strip() if isinstance(prompt, str) else None,
        },
        wait=True,
    )
    if result is None or not result.success:
        return (
            jsonify({"error": getattr(result, "error", "Failed to create tag")}),
            500,
        )

    tag_id = (result.data or {}).get("id")
    db.session.expire_all()
    tag = db.session.get(Tag, tag_id) if tag_id is not None else None
    if tag is None:
        return jsonify({"error": "Tag created but could not be loaded"}), 500
    return jsonify(_serialize_tag(tag)), 201


@tag_bp.route("/api/tags/<int:tag_id>", methods=["PATCH"])
def update_tag(tag_id: int) -> ResponseReturnValue:
    _, error_response = require_admin("update prompt tags")
    if error_response is not None:
        return error_response

    tag = Tag.query.get_or_404(tag_id)
    payload = request.get_json(silent=True) or {}
    updates: dict[str, Any] = {}

    if "name" in payload:
        name = payload["name"]
        if not isinstance(name, str) or not name.strip():
            return jsonify({"error": "name must be a non-empty string"}), 400
        trimmed_name = name.strip()
        existing = Tag.query.filter_by(name=trimmed_name).first()
        if existing is not None and existing.id != tag.id:
            return jsonify({"error": "A tag with that name already exists"}), 409
        updates["name"] = trimmed_name

    if "prompt" in payload:
        prompt = payload["prompt"]
        if prompt is not None and not isinstance(prompt, str):
            return jsonify({"error": "prompt must be a string or null"}), 400
        updates["prompt"] = prompt.strip() if isinstance(prompt, str) else None

    if not updates:
        return jsonify({"error": "No updates provided"}), 400

    result = writer_client.update("Tag", tag_id, updates, wait=True)
    if result is None or not result.success:
        return (
            jsonify({"error": getattr(result, "error", "Failed to update tag")}),
            500,
        )

    db.session.expire_all()
    tag = db.session.get(Tag, tag_id)
    if tag is None:
        return jsonify({"error": "Tag not found"}), 404
    return jsonify(_serialize_tag(tag))


@tag_bp.route("/api/tags/<int:tag_id>", methods=["DELETE"])
def delete_tag(tag_id: int) -> ResponseReturnValue:
    _, error_response = require_admin("delete prompt tags")
    if error_response is not None:
        return error_response

    Tag.query.get_or_404(tag_id)
    result = writer_client.delete("Tag", tag_id, wait=True)
    if result is None or not result.success:
        return (
            jsonify({"error": getattr(result, "error", "Failed to delete tag")}),
            500,
        )
    return jsonify({"status": "deleted", "id": tag_id})
