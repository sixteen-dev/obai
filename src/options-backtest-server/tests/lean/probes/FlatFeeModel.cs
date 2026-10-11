using System;
using QuantConnect;
using QuantConnect.Orders;
using QuantConnect.Orders.Fees;
using QuantConnect.Securities;

namespace ObaiLean.Probes
{
    /// <summary>
    /// $1.00 per contract of every order, $0 on an option exercise or assignment: the fee
    /// schedule illustrative_flat_1usd_per_contract_side_v1 (ADR 0002 §12 step 4, §17 item 28).
    /// A combo order's legs are separate orders, each charged on its own quantity.
    /// </summary>
    public class FlatFeeModel : FeeModel
    {
        /// <summary>Returns the order's fee in US dollars.</summary>
        /// <param name="parameters">The order and its security.</param>
        /// <returns>$1.00 × |quantity|, or $0 for an option exercise order.</returns>
        public override OrderFee GetOrderFee(OrderFeeParameters parameters)
        {
            ArgumentNullException.ThrowIfNull(parameters);
            var order = parameters.Order;
            var fee = order.Type == OrderType.OptionExercise ? 0m : order.AbsoluteQuantity;
            return new OrderFee(new CashAmount(fee, Currencies.USD));
        }
    }
}
