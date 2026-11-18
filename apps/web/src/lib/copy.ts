// Plain-language copy for mapped codes. The UI never shows raw server or database errors.

export const ERROR_COPY: Record<string, string> = {
  UNAUTHENTICATED: "Your session has ended. Please sign in again.",
  CSRF: "That request could not be verified. Reload the page and try again.",
  FORBIDDEN: "You do not have access to that.",
  NOT_FOUND: "That item was not found.",
  RATE_LIMITED: "You are asking too quickly. Wait a minute and try again.",
  BAD_REQUEST: "That question could not be processed. Try shortening it.",
  UNAVAILABLE: "Copilot is unavailable right now. Nothing was changed.",
  INTERNAL: "Something went wrong. Nothing was changed.",
  ROLE_REQUIRED: "Only approvers can decide drafts.",
  NOT_ENTITLED: "You are not entitled to decide drafts for this entity.",
  SOD_VIOLATION: "You cannot approve a draft you made or submitted.",
  NOT_SUBMITTED: "This draft has not been submitted for approval.",
  STEP_UP_REQUIRED: "Stronger, more recent sign-in is required to decide a draft.",
  ALREADY_DECIDED: "This draft has already been decided.",
};

export const errorCopy = (code: string): string => ERROR_COPY[code] ?? ERROR_COPY.INTERNAL!;

export const PROVENANCE_LABEL: Record<string, string> = {
  system_state: "System state",
  document: "Document",
  inference: "Inference",
  unknown: "Unverified",
};

export const STEP_LABEL: Record<string, string> = {
  route: "Understand the question",
  plan: "Plan the query",
  verify: "Check the plan against the catalogue",
  execute: "Run as you",
  analyse: "Check the numbers",
  narrate: "Write the answer",
  draft: "Prepare a draft",
};
