using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Text.Json;
using QuantConnect;
using QuantConnect.Algorithm;
using QuantConnect.Data;
using QuantConnect.Orders;
using QuantConnect.Securities;
using QuantConnect.Securities.Option;

namespace ObaiLean.Probes
{
    /// <summary>
    /// The scripted probe algorithm of the LEAN differential harness (ADR 0002 §12 step 3).
    ///
    /// Input, replay_input.json in LEAN's results folder: start_date and end_date
    /// (yyyy-MM-dd), cash (decimal string), contracts (our ids, "SPXW:yyyy-MM-dd:C|P:strike") and
    /// actions (<see cref="ProbeAction"/>). SPX is subscribed at minute resolution, each contract
    /// at minute resolution without fill-forward. Every security gets <see cref="FlatFeeModel"/>
    /// and BuyingPowerModel.Null, every option NullOptionAssignmentModel; settlement stays LEAN's
    /// default. An action runs in OnData when the slice time equals its time, in input order.
    ///
    /// Output, replay.jsonl in the same folder: an "order_event" record per order
    /// event, a "snapshot" record per snapshot action and a final "end" record (a snapshot plus
    /// "unexecuted", the actions that never ran). Decimals are invariant-culture strings, times
    /// New York wall clock.
    /// </summary>
    public class ProbeAlgorithm : QCAlgorithm
    {
        /// <summary>Input file name in LEAN's results folder.</summary>
        public const string InputName = "replay_input.json";

        /// <summary>Output file name in LEAN's results folder.</summary>
        public const string OutputName = "replay.jsonl";

        private readonly Dictionary<Symbol, string> _ids = new();
        private readonly Dictionary<string, Symbol> _symbols = new();
        private readonly Dictionary<string, List<OrderTicket>> _tickets = new();
        private readonly List<ProbeAction> _actions = new();
        private Symbol _index;
        private string _outputPath;

        /// <summary>Reads the input, sets the window and cash, subscribes the securities.</summary>
        public override void Initialize()
        {
            var folder = Globals.ResultsDestinationFolder;
            _outputPath = Path.Combine(folder, OutputName);
            using var input = JsonDocument.Parse(File.ReadAllText(Path.Combine(folder, InputName)));
            var root = input.RootElement;
            SetStartDate(ParseDate(root.GetProperty("start_date").GetString()));
            SetEndDate(ParseDate(root.GetProperty("end_date").GetString()));
            SetCash(ProbeAction.ParseDecimal(root.GetProperty("cash").GetString()));
            AddSecurityInitializer(ConfigureSecurity);
            _index = AddIndex("SPX", Resolution.Minute).Symbol;
            foreach (var contract in root.GetProperty("contracts").EnumerateArray())
            {
                AddContract(contract.GetString());
            }
            _actions.AddRange(root.GetProperty("actions").EnumerateArray().Select(ProbeAction.Parse));
        }

        /// <summary>Runs every pending action whose time is the slice time.</summary>
        /// <param name="slice">The slice.</param>
        public override void OnData(Slice slice)
        {
            foreach (var action in _actions.Where(action => !action.Executed && action.At == Time))
            {
                Execute(action);
                action.Executed = true;
            }
        }

        /// <summary>Writes one "order_event" record.</summary>
        /// <param name="orderEvent">The event.</param>
        public override void OnOrderEvent(OrderEvent orderEvent)
        {
            var order = Transactions.GetOrderById(orderEvent.OrderId);
            Emit(new Dictionary<string, object>
            {
                ["kind"] = "order_event",
                ["time"] = Stamp(Time),
                ["utc_time"] = Stamp(orderEvent.UtcTime),
                ["order_id"] = orderEvent.OrderId,
                ["tag"] = order.Tag,
                ["contract"] = IdOf(orderEvent.Symbol),
                ["order_type"] = order.Type.ToString(),
                ["status"] = orderEvent.Status.ToString(),
                ["fill_price"] = Text(orderEvent.FillPrice),
                ["fill_quantity"] = Text(orderEvent.FillQuantity),
                ["fee"] = Text(orderEvent.OrderFee.Value.Amount),
                ["is_assignment"] = orderEvent.IsAssignment,
                ["message"] = orderEvent.Message,
                ["index_price"] = Text(Securities[_index].Price),
                ["cash"] = Text(Portfolio.Cash),
            });
        }

        /// <summary>Writes the "end" record.</summary>
        public override void OnEndOfAlgorithm()
        {
            var record = Snapshot("end", "end");
            record["unexecuted"] = _actions
                .Where(action => !action.Executed)
                .Select(action => $"{action.Kind} {Stamp(action.At)} {action.Tag}")
                .ToList();
            Emit(record);
        }

        private void Execute(ProbeAction action)
        {
            switch (action.Kind)
            {
                case "combo_market":
                    _tickets[action.Tag] = ComboMarketOrder(Legs(action), action.Quantity, tag: action.Tag);
                    break;
                case "combo_limit":
                    _tickets[action.Tag] = ComboLimitOrder(Legs(action), action.Quantity, action.Limit, tag: action.Tag);
                    break;
                case "cancel":
                    Cancel(action.Tag);
                    break;
                case "snapshot":
                    Emit(Snapshot("snapshot", action.Tag));
                    break;
                default:
                    throw new ArgumentException($"unknown probe action kind '{action.Kind}'");
            }
        }

        private void Cancel(string tag)
        {
            foreach (var ticket in _tickets[tag].Where(ticket => ticket.Status.IsOpen()))
            {
                ticket.Cancel();
            }
        }

        private List<Leg> Legs(ProbeAction action)
        {
            return action.Legs.Select(leg => Leg.Create(_symbols[leg.Contract], leg.Ratio)).ToList();
        }

        private void AddContract(string id)
        {
            var parts = id.Split(':');
            if (parts.Length != 4 || parts[0] != "SPXW")
            {
                throw new ArgumentException($"probe contract '{id}' is not SPXW:yyyy-MM-dd:C|P:strike");
            }
            var right = parts[2] == "C" ? OptionRight.Call : OptionRight.Put;
            var symbol = QuantConnect.Symbol.CreateOption(
                _index, "SPXW", Market.USA, OptionStyle.European, right,
                ProbeAction.ParseDecimal(parts[3]), ParseDate(parts[1]));
            AddIndexOptionContract(symbol, Resolution.Minute, fillForward: false);
            _ids[symbol] = id;
            _symbols[id] = symbol;
        }

        private static void ConfigureSecurity(Security security)
        {
            security.SetFeeModel(new FlatFeeModel());
            security.SetBuyingPowerModel(BuyingPowerModel.Null);
            if (security is Option option)
            {
                option.SetOptionAssignmentModel(new NullOptionAssignmentModel());
            }
        }

        private Dictionary<string, object> Snapshot(string kind, string tag)
        {
            return new Dictionary<string, object>
            {
                ["kind"] = kind,
                ["tag"] = tag,
                ["time"] = Stamp(Time),
                ["cash"] = Text(Portfolio.Cash),
                ["unsettled_cash"] = Text(Portfolio.UnsettledCash),
                ["total_portfolio_value"] = Text(Portfolio.TotalPortfolioValue),
                ["index_price"] = Text(Securities[_index].Price),
                ["holdings"] = _ids.Select(pair => Holding(pair.Key, pair.Value)).ToList(),
            };
        }

        private Dictionary<string, object> Holding(Symbol symbol, string id)
        {
            var security = Securities[symbol];
            return new Dictionary<string, object>
            {
                ["contract"] = id,
                ["quantity"] = Text(security.Holdings.Quantity),
                ["price"] = Text(security.Price),
                ["bid"] = Text(security.BidPrice),
                ["ask"] = Text(security.AskPrice),
            };
        }

        private string IdOf(Symbol symbol)
        {
            return _ids.TryGetValue(symbol, out var id) ? id : symbol.Value;
        }

        private void Emit(Dictionary<string, object> record)
        {
            File.AppendAllText(_outputPath, JsonSerializer.Serialize(record) + "\n");
        }

        private static DateTime ParseDate(string text)
        {
            return DateTime.ParseExact(text, "yyyy-MM-dd", CultureInfo.InvariantCulture);
        }

        private static string Stamp(DateTime time)
        {
            return time.ToString(ProbeAction.TimeFormat, CultureInfo.InvariantCulture);
        }

        private static string Text(decimal value)
        {
            return value.ToString(CultureInfo.InvariantCulture);
        }
    }
}
