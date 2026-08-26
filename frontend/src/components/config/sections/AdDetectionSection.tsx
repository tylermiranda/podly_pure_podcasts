import { useConfigContext } from '../ConfigContext';
import { Section, Field, SaveButton } from '../shared';

export default function AdDetectionSection() {
  const { pending, setField, handleSave, isSaving } = useConfigContext();

  if (!pending) return null;

  const llm = pending.llm;

  return (
    <div className="space-y-6">
      <Section title="Ad detection reliability">
        <Field
          label="Boundary refine model (optional)"
          hint="Dedicated cheap model for boundary refinement; falls back to main LLM model when empty."
        >
          <input
            className="input"
            type="text"
            value={llm?.llm_boundary_refine_model ?? ''}
            onChange={(e) =>
              setField(['llm', 'llm_boundary_refine_model'], e.target.value || null)
            }
            placeholder={llm?.llm_model || 'Same as classify model'}
          />
        </Field>
        <Field label="Enable ad verify pass">
          <input
            type="checkbox"
            checked={!!llm?.enable_ad_verify}
            onChange={(e) => setField(['llm', 'enable_ad_verify'], e.target.checked)}
          />
        </Field>
        <Field label="Verify model (optional)">
          <input
            className="input"
            type="text"
            value={llm?.llm_verify_model ?? ''}
            onChange={(e) => setField(['llm', 'llm_verify_model'], e.target.value || null)}
            placeholder={llm?.llm_model || 'Same as classify model'}
          />
        </Field>
        <Field
          label="Two-stage classify"
          hint="Run LLM only on candidate spans (cues, creatives, audio FP, gaps, edges)."
        >
          <input
            type="checkbox"
            checked={!!llm?.enable_two_stage_classify}
            onChange={(e) => setField(['llm', 'enable_two_stage_classify'], e.target.checked)}
          />
        </Field>
        <Field label="Edge preroll seconds">
          <input
            className="input"
            type="number"
            value={llm?.two_stage_edge_preroll_seconds ?? 120}
            onChange={(e) =>
              setField(['llm', 'two_stage_edge_preroll_seconds'], Number(e.target.value))
            }
          />
        </Field>
        <Field label="Edge outro seconds">
          <input
            className="input"
            type="number"
            value={llm?.two_stage_edge_outro_seconds ?? 60}
            onChange={(e) =>
              setField(['llm', 'two_stage_edge_outro_seconds'], Number(e.target.value))
            }
          />
        </Field>
        <Field label="Enable audio fingerprint index">
          <input
            type="checkbox"
            checked={llm?.enable_ad_audio_fingerprint ?? true}
            onChange={(e) =>
              setField(['llm', 'enable_ad_audio_fingerprint'], e.target.checked)
            }
          />
        </Field>
        <Field label="Audio FP match threshold">
          <input
            className="input"
            type="number"
            step="0.01"
            min={0}
            max={1}
            value={llm?.ad_audio_fp_match_threshold ?? 0.15}
            onChange={(e) =>
              setField(['llm', 'ad_audio_fp_match_threshold'], Number(e.target.value))
            }
          />
        </Field>
        <Field label="Enable silence/gap detection">
          <input
            type="checkbox"
            checked={!!llm?.enable_ad_gap_detection}
            onChange={(e) => setField(['llm', 'enable_ad_gap_detection'], e.target.checked)}
          />
        </Field>
        <Field label="Gap min seconds">
          <input
            className="input"
            type="number"
            step="0.5"
            value={llm?.ad_gap_min_seconds ?? 4}
            onChange={(e) => setField(['llm', 'ad_gap_min_seconds'], Number(e.target.value))}
          />
        </Field>
        <Field label="Gap noise dB (ffmpeg silencedetect)">
          <input
            className="input"
            type="number"
            value={llm?.ad_gap_noise_db ?? -30}
            onChange={(e) => setField(['llm', 'ad_gap_noise_db'], Number(e.target.value))}
          />
        </Field>
        <Field label="Jingle min/max seconds">
          <div className="flex gap-2">
            <input
              className="input"
              type="number"
              step="0.5"
              value={llm?.jingle_min_seconds ?? 1}
              onChange={(e) => setField(['llm', 'jingle_min_seconds'], Number(e.target.value))}
            />
            <input
              className="input"
              type="number"
              step="0.5"
              value={llm?.jingle_max_seconds ?? 15}
              onChange={(e) => setField(['llm', 'jingle_max_seconds'], Number(e.target.value))}
            />
          </div>
        </Field>
      </Section>

      <SaveButton onSave={handleSave} isPending={isSaving} />

      <style>{`.input{width:100%;padding:0.5rem;border:1px solid #e5e7eb;border-radius:0.375rem;font-size:0.875rem}`}</style>
    </div>
  );
}
