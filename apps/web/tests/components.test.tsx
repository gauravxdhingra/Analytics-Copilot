import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AnswerView } from "../src/components/AnswerView";
import { Approvals } from "../src/components/Approvals";
import { HowDrawer } from "../src/components/HowDrawer";
import * as api from "../src/lib/api";
import { answered, refused, submitted } from "./fixtures";

afterEach(() => vi.restoreAllMocks());

describe("AnswerView", () => {
  it("shows the answer, the table and the as-of caveat", () => {
    render(<AnswerView answer={answered} onHow={() => {}} />);
    expect(screen.getByText(/12 booked transactions/)).toBeInTheDocument();
    expect(screen.getByRole("table")).toBeInTheDocument();
    expect(screen.getByText("Data is as of 2026-06-30.")).toBeInTheDocument();
  });

  it("labels every claim's evidence with its provenance", () => {
    render(<AnswerView answer={answered} onHow={() => {}} />);
    expect(screen.getByText(/system state/i)).toBeInTheDocument();
  });

  it("opens the drawer from the how-I-got-this button", async () => {
    const onHow = vi.fn();
    render(<AnswerView answer={answered} onHow={onHow} />);
    await userEvent.click(screen.getByRole("button", { name: /how i got this/i }));
    expect(onHow).toHaveBeenCalledOnce();
  });

  it("shows a refusal as blocked, with no table and no evidence", () => {
    render(<AnswerView answer={refused} onHow={() => {}} />);
    expect(screen.getByText(/outside what i can help with/i)).toBeInTheDocument();
    expect(screen.getByText(/blocked/i)).toBeInTheDocument();
    expect(screen.queryByRole("table")).toBeNull();
    expect(screen.queryByRole("button", { name: /how i got this/i })).toBeNull();
  });

  it("shows outside text without the wrapper tags, marked as external", () => {
    const wrapped = {
      ...answered,
      table: {
        ...answered.table!,
        columns: ["counterparty"],
        rows: [['<untrusted source="analytics.run_validated_select:name">Acme GmbH</untrusted>']],
      },
    };
    render(<AnswerView answer={wrapped} onHow={() => {}} />);
    expect(screen.getByText("Acme GmbH")).toBeInTheDocument();
    expect(screen.queryByText(/<untrusted/)).toBeNull();
    expect(screen.getByLabelText(/external text/i)).toBeInTheDocument();
  });

  it("renders model text as text, never as HTML", () => {
    const evil = { ...answered, text: '<img src=x onerror="window.__pwned=1">hello' };
    const { container } = render(<AnswerView answer={evil} onHow={() => {}} />);
    expect(container.querySelector("img")).toBeNull();
    expect(screen.getByText(/hello/)).toBeInTheDocument();
  });
});

describe("HowDrawer", () => {
  it("shows steps, the SQL with its parameters, and the data versions", async () => {
    vi.spyOn(api, "getTrace").mockResolvedValue([]);
    render(<HowDrawer answer={answered} onClose={() => {}} />);
    expect(screen.getByText(/SELECT entity_id/)).toBeInTheDocument();
    expect(screen.getByText(/booked/)).toBeInTheDocument();
    for (const step of ["route", "plan", "verify", "execute", "narrate"]) {
      expect(screen.getByText(step)).toBeInTheDocument();
    }
    expect(screen.getByText("catalogue")).toBeInTheDocument();
    await waitFor(() => expect(api.getTrace).toHaveBeenCalledWith("a".repeat(32)));
  });

  it("retries while the trace is still being exported, then shows it", async () => {
    const trace = vi
      .spyOn(api, "getTrace")
      .mockRejectedValueOnce(new api.ApiError("NOT_FOUND", 404))
      .mockResolvedValueOnce([{ span_id: "s1", parent_id: null, name: "agent.run", start_us: 0, duration_us: 5000, attributes: {} }]);
    render(<HowDrawer answer={answered} onClose={() => {}} retryMs={5} />);
    expect(await screen.findByText("agent.run")).toBeInTheDocument();
    expect(trace).toHaveBeenCalledTimes(2);
  });

  it("gives up after a few tries and says so", async () => {
    const trace = vi.spyOn(api, "getTrace").mockRejectedValue(new api.ApiError("NOT_FOUND", 404));
    render(<HowDrawer answer={answered} onClose={() => {}} retryMs={5} />);
    expect(await screen.findByText(/trace is unavailable/i)).toBeInTheDocument();
    expect(trace).toHaveBeenCalledTimes(4);
  });

  it("says so when the trace is unavailable, and still shows the rest", async () => {
    vi.spyOn(api, "getTrace").mockRejectedValue(new api.ApiError("UNAVAILABLE", 503));
    render(<HowDrawer answer={answered} onClose={() => {}} retryMs={5} />);
    expect(await screen.findByText(/trace is unavailable/i)).toBeInTheDocument();
    expect(screen.getByText(/SELECT entity_id/)).toBeInTheDocument();
  });
});

describe("Approvals", () => {
  it("lists the queue and approves with a comment", async () => {
    vi.spyOn(api, "approvalQueue").mockResolvedValue([submitted]);
    const decide = vi.spyOn(api, "decide").mockResolvedValue({ ...submitted, status: "approved" });
    render(<Approvals />);
    await userEvent.click(await screen.findByText("Review payments near the limit"));
    await userEvent.type(screen.getByLabelText(/comment/i), "ok");
    await userEvent.click(screen.getByRole("button", { name: /^approve/i }));
    await waitFor(() => expect(decide).toHaveBeenCalledWith(submitted.draft_id, "approve", "ok"));
  });

  it("offers step-up when the decision needs stronger authentication", async () => {
    vi.spyOn(api, "approvalQueue").mockResolvedValue([submitted]);
    vi.spyOn(api, "decide").mockRejectedValue(new api.ApiError("STEP_UP_REQUIRED", 403, "/auth/step-up?return=%2F"));
    render(<Approvals />);
    await userEvent.click(await screen.findByText("Review payments near the limit"));
    await userEvent.click(screen.getByRole("button", { name: /^approve/i }));
    const link = await screen.findByRole("link", { name: /verify it's you/i });
    expect(link).toHaveAttribute("href", "/auth/step-up?return=%2F");
  });

  it("explains a separation-of-duties refusal in plain words", async () => {
    vi.spyOn(api, "approvalQueue").mockResolvedValue([submitted]);
    vi.spyOn(api, "decide").mockRejectedValue(new api.ApiError("SOD_VIOLATION", 403));
    render(<Approvals />);
    await userEvent.click(await screen.findByText("Review payments near the limit"));
    await userEvent.click(screen.getByRole("button", { name: /^approve/i }));
    expect(await screen.findByText(/cannot approve a draft you made or submitted/i)).toBeInTheDocument();
  });
});
