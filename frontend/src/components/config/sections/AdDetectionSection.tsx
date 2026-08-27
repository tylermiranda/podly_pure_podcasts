import { useConfigContext } from '../ConfigContext';
import { Section, Field, SaveButton } from '../shared';

function CoachingCallout({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <div className="rounded-lg border border-blue-200 bg-blue-50 p-4 text-sm text-blue-950 text-left space-y-2">
      <h4 className="font-semibold text-blue-900">{title}</h4>
      {children}
    </div>
  );
}

export default function AdDetectionSection() {
  const { pending, setField, handleSave, isSaving } = useConfigContext();

  if (!pending) return null;

  const llm = pending.llm;
  const twoStageOn = !!llm?.enable_two_stage_classify;
  const gapOn = !!llm?.enable_ad_gap_detection;

  return (
    <div className="space-y-6">
      <CoachingCallout title="How to roll out ad detection features">
        <p className="text-blue-900">
          Turn on <strong>one knob at a time</strong>, reprocess a few episodes on feeds you watch,
          then check <strong>Stats → Overview → Ad Detection Signals</strong> on each episode.
          Leave a knob on only if cuts look as good or better — not because a count is high.
        </p>
        <ol className="list-decimal list-inside space-y-1 text-blue-900">
          <li>
            <strong>Baseline:</strong> boundary refine + verify (cheap flash models). Fix obvious
            missed ads / false cuts with transcript corrections first.
          </li>
          <li>
            <strong>Audio fingerprint</strong> (usually on): save repeating ad audio from Transcript
            corrections. Expect <em>Audio FP hits &gt; 0</em> after reprocess when creatives repeat.
          </li>
          <li>
            <strong>Two-stage classify:</strong> enable when baseline is good. Reprocess and confirm{' '}
            <em>Candidate spans</em> are &gt; 0 but well below total segments. If spans are 0, two-stage
            falls back to a full pass (safe, no savings).
          </li>
          <li>
            <strong>Gap detection:</strong> enable on episodes with music-only or untranscribed ad
            breaks. Reprocess and look for <em>Gap candidates &gt; 0</em>. Compare cuts before leaving
            it on — gaps alone do not auto-cut.
          </li>
          <li>
            <strong>Jingles:</strong> select a short intro/outro stinger in Transcript →{' '}
            <em>Save as jingle template</em>. Expect <em>Jingle hits</em> on later episodes.
          </li>
        </ol>
      </CoachingCallout>

      <Section title="Baseline quality">
        <Field
          label="Boundary refine model (optional)"
          hint="Fast cheap model for cut boundaries. Set this before enabling boundary refinement in LLM settings."
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
        <Field
          label="Enable ad verify pass"
          hint="Second LLM pass to catch missed ads and false positives. Keep on unless verify is slow or unreliable."
        >
          <input
            type="checkbox"
            checked={!!llm?.enable_ad_verify}
            onChange={(e) => setField(['llm', 'enable_ad_verify'], e.target.checked)}
          />
        </Field>
        <Field
          label="Auto-generate prompt tag on add"
          hint="When a new podcast is added, research RSS/directory/website and create or reuse a prompt tag, then assign it to the feed."
        >
          <input
            type="checkbox"
            checked={llm?.auto_generate_prompt_tag ?? true}
            onChange={(e) =>
              setField(['llm', 'auto_generate_prompt_tag'], e.target.checked)
            }
          />
        </Field>
        <Field
          label="Verify model (optional)"
          hint="Use the same fast flash model as boundary refine for verify when possible."
        >
          <input
            className="input"
            type="text"
            value={llm?.llm_verify_model ?? ''}
            onChange={(e) => setField(['llm', 'llm_verify_model'], e.target.value || null)}
            placeholder={llm?.llm_model || 'Same as classify model'}
          />
        </Field>
      </Section>

      <Section title="Audio fingerprint index">
        <Field
          label="Enable audio fingerprint index"
          hint="Chromaprint matching for saved ad creatives and jingles. Requires fpcalc in the container."
        >
          <input
            type="checkbox"
            checked={llm?.enable_ad_audio_fingerprint ?? true}
            onChange={(e) =>
              setField(['llm', 'enable_ad_audio_fingerprint'], e.target.checked)
            }
          />
        </Field>
        <Field
          label="Audio FP match threshold"
          hint="Lower = stricter matches (fewer hits). Raise only if real ads are missed; lower if you see false FP hits."
        >
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
        <Field
          label="Jingle min/max seconds"
          hint="Length window when scanning for saved jingle templates. Match typical intro/outro stinger length."
        >
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

      <Section title="Two-stage classify">
        {!twoStageOn && (
          <CoachingCallout title="Before enabling two-stage">
            <p>
              Only turn this on when baseline cuts are already acceptable. After enabling, reprocess
              and check Stats → <em>Candidate spans</em>. Good: &gt; 0 and much smaller than transcript
              segment count. Bad: 0 (no benefit) or spans covering most of the episode (higher miss risk).
            </p>
          </CoachingCallout>
        )}
        <Field
          label="Two-stage classify"
          hint="Run the classify LLM only on candidate regions (cues, creatives, audio FP, gaps, preroll/outro)."
        >
          <input
            type="checkbox"
            checked={twoStageOn}
            onChange={(e) => setField(['llm', 'enable_two_stage_classify'], e.target.checked)}
          />
        </Field>
        <Field
          label="Edge preroll seconds"
          hint="Always include the first N seconds as candidates (host-read preroll ads)."
        >
          <input
            className="input"
            type="number"
            value={llm?.two_stage_edge_preroll_seconds ?? 120}
            onChange={(e) =>
              setField(['llm', 'two_stage_edge_preroll_seconds'], Number(e.target.value))
            }
          />
        </Field>
        <Field
          label="Edge outro seconds"
          hint="Always include the last N seconds as candidates (outro ads and credits)."
        >
          <input
            className="input"
            type="number"
            value={llm?.two_stage_edge_outro_seconds ?? 60}
            onChange={(e) =>
              setField(['llm', 'two_stage_edge_outro_seconds'], Number(e.target.value))
            }
          />
        </Field>
      </Section>

      <Section title="Silence / gap detection">
        {!gapOn && (
          <CoachingCallout title="Before enabling gap detection">
            <p>
              Use on episodes where ads are mostly music or silence with little transcript. Enable,
              reprocess those episodes, and check Stats → <em>Gap candidates</em>. If still 0, gaps
              are probably not the issue — tune min seconds / noise dB or skip this knob.
            </p>
          </CoachingCallout>
        )}
        <Field
          label="Enable silence/gap detection"
          hint="Finds long audio regions with no transcript (music beds, untranscribed breaks). Feeds candidates and verify hints; does not auto-cut by itself."
        >
          <input
            type="checkbox"
            checked={gapOn}
            onChange={(e) => setField(['llm', 'enable_ad_gap_detection'], e.target.checked)}
          />
        </Field>
        <Field
          label="Gap min seconds"
          hint="Minimum untranscribed audio length to flag. Lower catches shorter music beds; higher reduces noise."
        >
          <input
            className="input"
            type="number"
            step="0.5"
            value={llm?.ad_gap_min_seconds ?? 4}
            onChange={(e) => setField(['llm', 'ad_gap_min_seconds'], Number(e.target.value))}
          />
        </Field>
        <Field
          label="Gap noise dB (ffmpeg silencedetect)"
          hint="Silence threshold for ffmpeg. More negative (-40) = stricter silence; less negative (-25) = more regions flagged."
        >
          <input
            className="input"
            type="number"
            value={llm?.ad_gap_noise_db ?? -30}
            onChange={(e) => setField(['llm', 'ad_gap_noise_db'], Number(e.target.value))}
          />
        </Field>
      </Section>

      <SaveButton onSave={handleSave} isPending={isSaving} />

      <style>{`.input{width:100%;padding:0.5rem;border:1px solid #e5e7eb;border-radius:0.375rem;font-size:0.875rem}`}</style>
    </div>
  );
}
