import { useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { tagsApi } from '../services/api';
import type { PromptTag } from '../types';

interface TagsManagerModalProps {
  isOpen: boolean;
  onClose: () => void;
}

export default function TagsManagerModal({ isOpen, onClose }: TagsManagerModalProps) {
  const queryClient = useQueryClient();
  const [editingId, setEditingId] = useState<number | null>(null);
  const [name, setName] = useState('');
  const [prompt, setPrompt] = useState('');
  const [formError, setFormError] = useState<string | null>(null);

  const { data: tags = [], isLoading } = useQuery({
    queryKey: ['tags'],
    queryFn: tagsApi.list,
    enabled: isOpen,
  });

  useEffect(() => {
    if (!isOpen) {
      setEditingId(null);
      setName('');
      setPrompt('');
      setFormError(null);
    }
  }, [isOpen]);

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

  if (!isOpen) return null;

  const isSaving = createMutation.isPending || updateMutation.isPending;

  return (
    <div className="fixed inset-0 z-[70] flex items-center justify-center p-4">
      <div className="absolute inset-0 bg-black/40" onClick={onClose} />

      <div className="relative w-full max-w-lg bg-white rounded-xl border border-gray-200 shadow-lg overflow-hidden max-h-[90vh] flex flex-col">
        <div className="flex items-start justify-between gap-4 px-5 py-4 border-b border-gray-200">
          <div>
            <h2 className="text-base font-semibold text-gray-900">Prompt Tags</h2>
            <p className="text-sm text-gray-600 mt-1">
              Reusable ad-detection prompts (e.g. noiser) you can assign to feeds.
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="text-gray-400 hover:text-gray-600"
          >
            <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>

        <div className="px-5 py-4 space-y-4 overflow-y-auto">
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
            {formError && (
              <p className="text-sm text-red-700">{formError}</p>
            )}
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
                          if (confirm(`Delete tag "${tag.name}"? Feeds using it will be unassigned.`)) {
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

        <div className="flex justify-end gap-3 px-5 py-4 border-t border-gray-200 bg-gray-50">
          <button
            type="button"
            onClick={onClose}
            className="px-4 py-2 text-sm font-medium text-gray-700 bg-white border border-gray-300 rounded-lg hover:bg-gray-50"
          >
            Close
          </button>
        </div>
      </div>
    </div>
  );
}
