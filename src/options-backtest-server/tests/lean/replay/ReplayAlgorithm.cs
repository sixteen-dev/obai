using ObaiLean.Probes;

namespace ObaiLean.Replay
{
    /// <summary>
    /// The LEAN replay of one of our runs (ADR 0002 §12 step 4 as amended by §16 decision 3).
    ///
    /// It is <see cref="ProbeAlgorithm"/> unchanged, compiled from the probe sources: the probes
    /// verified exactly this machinery (every fill and 16:00 mark echoes the export, a cash-settled
    /// expiry settles at the official value, combo limits are strict), so the replay inherits that
    /// evidence instead of re-implementing it. Its security initializer is the replay's: a $1 ×
    /// |quantity| fee model with $0 on OptionExercise (<see cref="FlatFeeModel"/>),
    /// BuyingPowerModel.Null, NullOptionAssignmentModel and LEAN's default immediate settlement.
    ///
    /// What makes it a replay is its input, replay_input.json, which tests/lean/reconcile.py
    /// derives from our artifacts: each of our fills a combo market order at its fill instant,
    /// then a snapshot; each order we cancelled after missing its limit at F1-F3 a combo limit at
    /// its DEC instant, cancelled at F3 (diagnostic); a snapshot at every session's close. The
    /// output, replay.jsonl, holds every order event, every snapshot's cash, unsettled cash, total
    /// portfolio value and holdings, and the end record.
    /// </summary>
    public sealed class ReplayAlgorithm : ProbeAlgorithm
    {
    }
}
