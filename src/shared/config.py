from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

from shared import defaults as DEFAULTS


class ProcessingConfig(BaseModel):
    num_segments_to_input_to_prompt: int
    max_overlap_segments: int = Field(
        default=DEFAULTS.PROCESSING_MAX_OVERLAP_SEGMENTS,
        ge=0,
        description="Maximum number of previously identified segments carried into the next prompt.",
    )

    @model_validator(mode="after")
    def validate_overlap_limits(self) -> ProcessingConfig:
        assert self.max_overlap_segments <= self.num_segments_to_input_to_prompt, (
            "max_overlap_segments must be <= num_segments_to_input_to_prompt"
        )
        return self


class OutputConfig(BaseModel):
    fade_ms: int
    min_ad_segement_separation_seconds: int
    min_ad_segment_length_seconds: int
    min_confidence: float

    @property
    def min_ad_segment_separation_seconds(self) -> int:
        """Backwards-compatible alias for the misspelled config field."""
        return self.min_ad_segement_separation_seconds

    @min_ad_segment_separation_seconds.setter
    def min_ad_segment_separation_seconds(self, value: int) -> None:
        self.min_ad_segement_separation_seconds = value


WhisperConfigTypes = Literal["remote", "local", "test", "groq"]


class TestWhisperConfig(BaseModel):
    whisper_type: Literal["test"] = "test"


class RemoteWhisperConfig(BaseModel):
    whisper_type: Literal["remote"] = "remote"
    base_url: str = DEFAULTS.WHISPER_REMOTE_BASE_URL
    api_key: str
    language: str = DEFAULTS.WHISPER_REMOTE_LANGUAGE
    model: str = DEFAULTS.WHISPER_REMOTE_MODEL
    timeout_sec: int = DEFAULTS.WHISPER_REMOTE_TIMEOUT_SEC
    chunksize_mb: int = DEFAULTS.WHISPER_REMOTE_CHUNKSIZE_MB


class GroqWhisperConfig(BaseModel):
    whisper_type: Literal["groq"] = "groq"
    api_key: str
    language: str = DEFAULTS.WHISPER_GROQ_LANGUAGE
    model: str = DEFAULTS.WHISPER_GROQ_MODEL
    max_retries: int = DEFAULTS.WHISPER_GROQ_MAX_RETRIES


class LocalWhisperConfig(BaseModel):
    whisper_type: Literal["local"] = "local"
    model: str = DEFAULTS.WHISPER_LOCAL_MODEL


class Config(BaseModel):
    llm_api_key: str | None = Field(default=None)
    llm_model: str = Field(default=DEFAULTS.LLM_DEFAULT_MODEL)
    openai_base_url: str | None = None
    openai_max_tokens: int = DEFAULTS.OPENAI_DEFAULT_MAX_TOKENS
    openai_timeout: int = DEFAULTS.OPENAI_DEFAULT_TIMEOUT_SEC
    # Optional: Rate limiting controls
    llm_max_concurrent_calls: int = Field(
        default=DEFAULTS.LLM_DEFAULT_MAX_CONCURRENT_CALLS,
        description="Maximum concurrent LLM calls to prevent rate limiting",
    )
    llm_max_retry_attempts: int = Field(
        default=DEFAULTS.LLM_DEFAULT_MAX_RETRY_ATTEMPTS,
        description="Maximum retry attempts for failed LLM calls",
    )
    llm_max_input_tokens_per_call: int | None = Field(
        default=DEFAULTS.LLM_MAX_INPUT_TOKENS_PER_CALL,
        description="Maximum input tokens per LLM call to stay under API limits",
    )
    # Token-based rate limiting
    llm_enable_token_rate_limiting: bool = Field(
        default=DEFAULTS.LLM_ENABLE_TOKEN_RATE_LIMITING,
        description="Enable client-side token-based rate limiting",
    )
    llm_max_input_tokens_per_minute: int | None = Field(
        default=DEFAULTS.LLM_MAX_INPUT_TOKENS_PER_MINUTE,
        description="Override default tokens per minute limit for the model",
    )
    enable_boundary_refinement: bool = Field(
        default=DEFAULTS.ENABLE_BOUNDARY_REFINEMENT,
        description="Enable LLM-based ad boundary refinement for improved precision (consumes additional LLM tokens)",
    )
    enable_word_level_boundary_refinder: bool = Field(
        default=DEFAULTS.ENABLE_WORD_LEVEL_BOUNDARY_REFINDER,
        description="Enable word-level (heuristic-timed) ad boundary refinement",
    )
    enable_llm_chapter_fallback_tagging: bool = Field(
        default=DEFAULTS.ENABLE_LLM_CHAPTER_FALLBACK_TAGGING,
        description=(
            "When enabled, LLM processing will preserve embedded chapters or "
            "generate fallback chapter tags from description/transcript."
        ),
    )
    enable_ad_verify: bool = Field(
        default=DEFAULTS.ENABLE_AD_VERIFY,
        description=(
            "Run a second-pass LLM verify over draft ad windows and store "
            "results in refined_ad_boundaries (shared Stats + ffmpeg path)."
        ),
    )
    auto_generate_prompt_tag: bool = Field(
        default=DEFAULTS.AUTO_GENERATE_PROMPT_TAG,
        description=(
            "When a new feed is added, research the show and create/assign a "
            "reusable prompt Tag (Feed.prompt_tag_id) for ad classification."
        ),
    )
    llm_verify_model: str | None = Field(
        default=DEFAULTS.LLM_VERIFY_MODEL,
        description=(
            "Optional dedicated model for ad verify. Falls back to llm_model when unset."
        ),
    )
    llm_boundary_refine_model: str | None = Field(
        default=DEFAULTS.LLM_BOUNDARY_REFINE_MODEL,
        description=(
            "Optional dedicated model for ad boundary refinement. "
            "Falls back to llm_model when unset."
        ),
    )
    enable_two_stage_classify: bool = Field(
        default=DEFAULTS.ENABLE_TWO_STAGE_CLASSIFY,
        description=(
            "When enabled, run LLM classify only on candidate spans "
            "(cues, creatives, audio FP, gaps, edges)."
        ),
    )
    two_stage_edge_preroll_seconds: int = Field(
        default=DEFAULTS.TWO_STAGE_EDGE_PREROLL_SECONDS,
        ge=0,
        description="Always include the first N seconds as classify candidates.",
    )
    two_stage_edge_outro_seconds: int = Field(
        default=DEFAULTS.TWO_STAGE_EDGE_OUTRO_SECONDS,
        ge=0,
        description="Always include the last N seconds as classify candidates.",
    )
    two_stage_candidate_pad_segments: int = Field(
        default=DEFAULTS.TWO_STAGE_CANDIDATE_PAD_SEGMENTS,
        ge=0,
        description="Pad candidate spans by this many transcript segments for LLM context.",
    )
    enable_ad_audio_fingerprint: bool = Field(
        default=DEFAULTS.ENABLE_AD_AUDIO_FINGERPRINT,
        description="Index and match Chromaprint audio fingerprints for repeated ads.",
    )
    ad_audio_fp_match_threshold: float = Field(
        default=DEFAULTS.AD_AUDIO_FP_MATCH_THRESHOLD,
        ge=0.0,
        le=1.0,
        description="Maximum normalized bit distance for audio fingerprint match.",
    )
    ad_audio_fp_min_duration_seconds: float = Field(
        default=DEFAULTS.AD_AUDIO_FP_MIN_DURATION_SECONDS,
        ge=0.5,
        description="Minimum audio window duration to fingerprint for creatives.",
    )
    enable_ad_gap_detection: bool = Field(
        default=DEFAULTS.ENABLE_AD_GAP_DETECTION,
        description="Detect non-silent audio with no transcript as ad candidates.",
    )
    ad_gap_min_seconds: float = Field(
        default=DEFAULTS.AD_GAP_MIN_SECONDS,
        ge=0.5,
        description="Minimum duration for suspicious audio-only gaps.",
    )
    ad_gap_noise_db: int = Field(
        default=DEFAULTS.AD_GAP_NOISE_DB,
        description="ffmpeg silencedetect noise threshold (dB).",
    )
    enable_ad_gap_auto_cut: bool = Field(
        default=DEFAULTS.ENABLE_AD_GAP_AUTO_CUT,
        description="Auto-label audio gaps as ads without LLM confirmation (aggressive).",
    )
    jingle_min_seconds: float = Field(
        default=DEFAULTS.JINGLE_MIN_SECONDS,
        ge=0.5,
        description="Minimum jingle template duration.",
    )
    jingle_max_seconds: float = Field(
        default=DEFAULTS.JINGLE_MAX_SECONDS,
        ge=1.0,
        description="Maximum jingle template duration.",
    )
    ad_creative_min_chars: int = Field(
        default=DEFAULTS.AD_CREATIVE_MIN_CHARS,
        ge=8,
        description="Minimum normalized creative length to index / match.",
    )
    ad_creative_jaccard: float = Field(
        default=DEFAULTS.AD_CREATIVE_JACCARD,
        ge=0.0,
        le=1.0,
        description="Token Jaccard threshold for fuzzy creative matching.",
    )
    developer_mode: bool = Field(
        default=False,
        description="Enable developer mode features like test feeds",
    )
    output: OutputConfig
    processing: ProcessingConfig
    server: str | None = Field(
        default=None,
        deprecated=True,
        description="deprecated in favor of request-aware URL generation",
    )
    background_update_interval_minute: int | None = (
        DEFAULTS.APP_BACKGROUND_UPDATE_INTERVAL_MINUTE
    )
    post_cleanup_retention_days: int | None = Field(
        default=DEFAULTS.APP_POST_CLEANUP_RETENTION_DAYS,
        description="Number of days to retain processed post data before cleanup. None disables cleanup.",
    )
    # removed job_timeout
    whisper: (
        LocalWhisperConfig
        | RemoteWhisperConfig
        | TestWhisperConfig
        | GroqWhisperConfig
        | None
    ) = Field(
        default=None,
        discriminator="whisper_type",
    )
    remote_whisper: bool | None = Field(
        default=False,
        deprecated=True,
        description="deprecated in favor of [Remote|Local]WhisperConfig",
    )
    whisper_model: str | None = Field(
        default=DEFAULTS.WHISPER_LOCAL_MODEL,
        deprecated=True,
        description="deprecated in favor of [Remote|Local]WhisperConfig",
    )
    automatically_whitelist_new_episodes: bool = (
        DEFAULTS.APP_AUTOMATICALLY_WHITELIST_NEW_EPISODES
    )
    number_of_episodes_to_whitelist_from_archive_of_new_feed: int = (
        DEFAULTS.APP_NUM_EPISODES_TO_WHITELIST_FROM_ARCHIVE_OF_NEW_FEED
    )
    enable_public_landing_page: bool = DEFAULTS.APP_ENABLE_PUBLIC_LANDING_PAGE
    user_limit_total: int | None = DEFAULTS.APP_USER_LIMIT_TOTAL
    autoprocess_on_download: bool = DEFAULTS.APP_AUTOPROCESS_ON_DOWNLOAD
    cost_rate_per_hour: float = DEFAULTS.APP_COST_RATE_PER_HOUR
    feed_title_prefix: str = DEFAULTS.APP_FEED_TITLE_PREFIX

    def redacted(self) -> Config:
        return self.model_copy(
            update={
                "llm_api_key": "X" * 10,
            },
            deep=True,
        )

    @model_validator(mode="after")
    def validate_whisper_config(self) -> Config:
        new_style = self.whisper is not None

        if new_style:
            self.whisper_model = None
            self.remote_whisper = None
            return self

        # if we have old style, change to the equivalent new style
        if self.remote_whisper:
            assert self.llm_api_key is not None, (
                "must supply api key to use remote whisper"
            )
            self.whisper = RemoteWhisperConfig(
                api_key=self.llm_api_key,
                base_url=self.openai_base_url or "https://api.openai.com/v1",
            )
        else:
            assert self.whisper_model is not None, (
                "must supply whisper model to use local whisper"
            )
            self.whisper = LocalWhisperConfig(model=self.whisper_model)

        self.whisper_model = None
        self.remote_whisper = None

        return self
