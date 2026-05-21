/** Demo data for the Dashboard preview.
 *  Until real bank statements / invoices flow through the system, the Dashboard
 *  shows this synthetic but realistic data so the UI is a meaningful preview.
 *
 *  Toggle it off in the UI to see the empty / real-data state.
 */

import { subDays, format } from "date-fns";

export const demo = {
  cashPosition: 4230000, // ₹42.30L
  cashPositionPrev: 3820000,
  receivablesTotal: 1840000,
  receivablesAging: [
    { bucket: "0–30", amount: 620000 },
    { bucket: "31–60", amount: 480000 },
    { bucket: "61–90", amount: 310000 },
    { bucket: "90+", amount: 430000 },
  ],
  payablesTotal: 980000,
  payablesPrev: 1100000,
  monthExpense: 1340000,
  monthRevenue: 2680000,

  // 30 days of cash in / cash out
  cashFlow: Array.from({ length: 30 }).map((_, i) => {
    const date = subDays(new Date(), 29 - i);
    // Pseudo-random but deterministic
    const seed = i + 7;
    const inAmt = 60000 + ((seed * 9301 + 49297) % 90000);
    const outAmt = 35000 + ((seed * 4567 + 12345) % 80000);
    return {
      date: format(date, "MMM d"),
      in: inAmt,
      out: outAmt,
      net: inAmt - outAmt,
    };
  }),

  expenseByCategory: [
    { name: "Payroll", value: 540000, color: "#6366f1" },
    { name: "Rent", value: 160000, color: "#8b5cf6" },
    { name: "Raw material", value: 240000, color: "#06b6d4" },
    { name: "Marketing", value: 95000, color: "#f59e0b" },
    { name: "Travel", value: 64000, color: "#10b981" },
    { name: "Other", value: 241000, color: "#94a3b8" },
  ],

  topVendors: [
    { name: "ABC Traders Pvt Ltd", amount: 340000, deltaPct: 18 },
    { name: "XYZ Office Supplies", amount: 210000, deltaPct: 0 },
    { name: "Citi Cabs", amount: 84000, deltaPct: -12 },
    { name: "Cloud Provider Co.", amount: 76000, deltaPct: 8 },
    { name: "DigitalAd Agency", amount: 62000, deltaPct: 4 },
  ],

  topClients: [
    { name: "Acme Corp", amount: 1420000, deltaPct: -22 },
    { name: "Globex Ltd", amount: 850000, deltaPct: 40 },
    { name: "Initech LLP", amount: 540000, deltaPct: 12 },
    { name: "Hooli Pvt Ltd", amount: 410000, deltaPct: -8 },
  ],

  insights: [
    {
      id: "1",
      severity: "urgent" as const,
      title: "Receivable overdue — Acme Corp",
      body: "₹3.4L · 47 days past due. Last payment took 61 days; collection likelihood 28%.",
      time: "12m ago",
    },
    {
      id: "2",
      severity: "attention" as const,
      title: "Vendor cost rising — ABC Traders",
      body: "Billed 38% above their 6-month average this month.",
      time: "1h ago",
    },
    {
      id: "3",
      severity: "info" as const,
      title: "Cash forecast updated",
      body: "Projected cash on Jul 18: ₹14.8L (range ₹11–19L). Tracking below ₹15L floor.",
      time: "3h ago",
    },
    {
      id: "4",
      severity: "info" as const,
      title: "GST Q1 projection",
      body: "Net GST payable projected at ₹9.8L ± ₹0.6L. Quarter ends in 27 days.",
      time: "yesterday",
    },
  ],

  forecastSeries: Array.from({ length: 30 }).map((_, i) => {
    const date = format(subDays(new Date(), -i), "MMM d");
    const base = 4230000 - i * 20000 + Math.sin(i / 4) * 80000;
    return {
      date,
      forecast: base,
      lowerBand: base - 150000 - i * 4000,
      upperBand: base + 150000 + i * 4000,
    };
  }),
};

export type DemoData = typeof demo;
