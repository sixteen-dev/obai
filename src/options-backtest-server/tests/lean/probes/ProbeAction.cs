using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using System.Text.Json;

namespace ObaiLean.Probes
{
    /// <summary>
    /// One scripted step of a probe, parsed from replay_input.json. Every field is required
    /// for the kinds that use it; a missing or malformed field throws.
    /// </summary>
    public sealed class ProbeAction
    {
        /// <summary>New York wall-clock format of every instant in the probe files.</summary>
        public const string TimeFormat = "yyyy-MM-ddTHH:mm:ss";

        /// <summary>Slice time (New York) at which the action runs.</summary>
        public DateTime At { get; private init; }

        /// <summary>combo_market, combo_limit, cancel or snapshot.</summary>
        public string Kind { get; private init; }

        /// <summary>Order tag (combo kinds, cancel) or snapshot label.</summary>
        public string Tag { get; private init; }

        /// <summary>(contract id, signed ratio) per leg, for the combo kinds.</summary>
        public IReadOnlyList<(string Contract, int Ratio)> Legs { get; private init; }

        /// <summary>Package count of a combo order.</summary>
        public int Quantity { get; private init; }

        /// <summary>Combo limit price per unit package (Σ ratio × price), for combo_limit.</summary>
        public decimal Limit { get; private init; }

        /// <summary>Whether the action has run.</summary>
        public bool Executed { get; set; }

        /// <summary>Parses one element of the input's "actions" array.</summary>
        /// <param name="element">The action object.</param>
        /// <returns>The action, not yet executed.</returns>
        public static ProbeAction Parse(JsonElement element)
        {
            var kind = element.GetProperty("kind").GetString();
            var isCombo = kind == "combo_market" || kind == "combo_limit";
            return new ProbeAction
            {
                At = ParseTime(element.GetProperty("at").GetString()),
                Kind = kind,
                Tag = element.GetProperty("tag").GetString(),
                Legs = isCombo ? ParseLegs(element.GetProperty("legs")) : Array.Empty<(string, int)>(),
                Quantity = isCombo ? element.GetProperty("quantity").GetInt32() : 0,
                Limit = kind == "combo_limit" ? ParseDecimal(element.GetProperty("limit").GetString()) : 0m,
            };
        }

        /// <summary>Parses a New York wall-clock instant.</summary>
        /// <param name="text">yyyy-MM-ddTHH:mm:ss.</param>
        /// <returns>The instant.</returns>
        public static DateTime ParseTime(string text)
        {
            return DateTime.ParseExact(text, TimeFormat, CultureInfo.InvariantCulture);
        }

        /// <summary>Parses an exact decimal string.</summary>
        /// <param name="text">A decimal such as "-0.90".</param>
        /// <returns>The value.</returns>
        public static decimal ParseDecimal(string text)
        {
            return decimal.Parse(text, NumberStyles.Number, CultureInfo.InvariantCulture);
        }

        private static IReadOnlyList<(string, int)> ParseLegs(JsonElement legs)
        {
            return legs.EnumerateArray()
                .Select(leg => (leg[0].GetString(), leg[1].GetInt32()))
                .ToList();
        }
    }
}
