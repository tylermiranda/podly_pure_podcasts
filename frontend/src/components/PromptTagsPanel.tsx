import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { tagsApi } from '../services/api';
import type { PromptTag } from '../types';

interface PromptTagsPanelProps {
  /** When false, skip fetching (e.g. closed modal). Defaults to true. */
  enabled?: boolean;
}

export default function PromptTagsPanel({ enabled = true }: PromptTagsPanelProps) {
  const queryClient = useQueryClient();
  const [editingId, setEditingId] = useState<number | null>(null);
  const [name, setName] = useState('');
  const [prompt, setPrompt] = useState('');
  const [formError, setFormError] = useState<string | null>(null);

  const { data: tags = [], isLoading } = useQuery({
    queryKey: ['tags'],
    queryFn: tagsApi.list,
    enabled,
  });

  const resetForm = () => {
    setEditingId(null);
    setName('');
    setPrompt('');
    setFormError(null);
  };

  const createMutation = useMutation({
    mutationFn: () => tagsApi.create({ name, prompt: prompt || null }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['tags'] });
      queryClient.invalidateQueries({ queryKey: ['feeds'] });
      resetForm();
    },
    onError: () => setFormError('Failed to create tag.'),
  });

  const updateMutation = useMutation({
    mutationFn: () => {
      if (editingId == null) {
        throw new Error('No tag selected');
      }
      return tagsApi.update(editingId, { name, prompt: prompt || null });
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['tags'] });
      queryClient.invalidateQueries({ queryKey: ['feeds'] });
      resetForm();
    },
    onError: () => setFormError('Failed to update tag.'),
  });

  const deleteMutation = useMutation({
    mutationFn: (tagId: number) => tagsApi.delete(tagId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['tags'] });
      queryClient.invalidateQueries({ queryKey: ['feeds'] });
      resetForm();
    },
    onError: () => setFormError('Failed to delete tag.'),
  });

  const startEdit = (tag: PromptTag) => {
    setEditingId(tag.id);
    setName(tag.name);
    setPrompt(tag.prompt || '');
    setFormError(null);
  };

  const handleSave = () => {
    if (!name.trim()) {
      setFormError('Name is required.');
      return;
    }
    if (editingId == null) {
      createMutation.mutate();
    } else {
      updateMutation.mutate();
    }
  };

  const isSaving = createMutation.isPending || updateMutation.isPending;

  return (
    <div className="space-y-4">
      <div className="space-y-2">
        <label className="block text-sm font-medium text-gray-700">
          {editingId == null ? 'New tag' : 'Edit tag'}
        </label>
        <input
          type="text"
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="e.g. noiser"
          className="w-full rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm text-gray-900 focus:border-blue-500 focus:outline-none focus:ring-2 focus:ring-blue-200"
        />
        <textarea
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
          placeholder="Prompt instructions for this production company / pattern"
          rows={4}
          className="w-full rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm text-gray-900 focus:border-blue-500 focus:outline-none focus:ring-2 focus:ring-blue-200"
        />
        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            onClick={handleSave}
            disabled={isSaving}
            className="px-3 py-1.5 text-sm font-medium text-white bg-blue-600 rounded-lg hover:bg-blue-700 disabled:opacity-50"
          >
            {isSaving ? 'Saving...' : editingId == null ? 'Create tag' : 'Save changes'}
          </button>
          {editingId != null && (
            <button
              type="button"
              onClick={resetForm}
              className="px-3 py-1.5 text-sm font-medium text-gray-700 bg-white border border-gray-300 rounded-lg hover:bg-gray-50"
            >
              Cancel edit
            </button>
          )}
        </div>
        {formError && <p className="text-sm text-red-700">{formError}</p>}
      </div>

      <div className="border-t border-gray-200 pt-4">
        <h3 className="text-sm font-medium text-gray-700 mb-2">Existing tags</h3>
        {isLoading ? (
          <p className="text-sm text-gray-500">Loading…</p>
        ) : tags.length === 0 ? (
          <p className="text-sm text-gray-500">No tags yet.</p>
        ) : (
          <ul className="space-y-2">
            {tags.map((tag) => (
              <li
                key={tag.id}
                className="rounded-lg border border-gray-200 px-3 py-2 flex items-start justify-between gap-3"
              >
                <div className="min-w-0">
                  <div className="text-sm font-semibold text-gray-900">{tag.name}</div>
                  <div className="text-xs text-gray-500 mt-0.5 line-clamp-2">
                    {tag.prompt?.trim() || 'No prompt text'}
                  </div>
                </div>
                <div className="flex shrink-0 gap-2">
                  <button
                    type="button"
                    onClick={() => startEdit(tag)}
                    className="text-xs font-medium text-blue-700 hover:text-blue-900"
                  >
                    Edit
                  </button>
                  <button
                    type="button"
                    onClick={() => {
                      if (
                        confirm(
                          `Delete tag "${tag.name}"? Feeds using it will be unassigned.`
                        )
                      ) {
                        deleteMutation.mutate(tag.id);
                      }
                    }}
                    className="text-xs font-medium text-red-600 hover:text-red-800"
                  >
                    Delete
                  </button>
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
