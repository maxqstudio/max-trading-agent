//+------------------------------------------------------------------+
//| Max_MTF.mq5                               |
//| Max MTF v2.11 · TRUE_MTF_DYNAMIC_V1 Strategy                    |
//+------------------------------------------------------------------+
#property strict
#property tester_no_cache
#property version   "2.11"
#property description "Max MTF v2.11: true dynamic multi-timeframe 7-family Strategy; closed-bar causal roles; deterministic risk + ONNX runtime; optimizer fitness V2."

#include <Trade/Trade.mqh>

#define MAX_MTF_EA_MAJOR_VERSION 2
#define MAX_MTF_FOUNDATION_PHASE 7
#define MAX_MTF_STRATEGY_CONTRACT "MAX_TRUE_MTF_DYNAMIC_V1"
#define MAX_MTF_RESOLVER_VERSION "TRUE_MTF_LOG_RATIO_V1"
#define MAX_MTF_FEATURE_CONTRACT "CP32_TRUE_MTF_V1"
#define MTF_ROLE_COUNT 4
#define FAMILY_COUNT        7
#define ONNX_FEATURE_COUNT 32
#define ONNX_CLASS_COUNT    3
#define DIRECTION_CLASS_COUNT 2
#define HYBRID_POLICY_FEATURE_COUNT 36
#define ONNX_MAX_SEQUENCE   64
#define TRAINING_COLUMN_COUNT 49

enum ENUM_FAMILY
  {
   FAMILY_TREND=0,
   FAMILY_RANGE=1,
   FAMILY_BREAKOUT=2,
   FAMILY_PULLBACK=3,
   FAMILY_SESSION=4,
   FAMILY_SHOCK=5,
   FAMILY_RELATIVE=6
  };

enum ENUM_MAX_ONNX_MODEL_FAMILY
  {
   MODEL_FAMILY_AUTO=0,
   MODEL_FAMILY_LIGHTGBM=1,
   MODEL_FAMILY_XGBOOST=2,
   MODEL_FAMILY_RANDOM_FOREST=3,
   MODEL_FAMILY_GRU=4,
   MODEL_FAMILY_LSTM=5,
   MODEL_FAMILY_TCN=6,
   MODEL_FAMILY_TRANSFORMER_ENCODER=7,
   MODEL_FAMILY_PATCHTST=8,
   MODEL_FAMILY_ITRANSFORMER=9,
   MODEL_FAMILY_TFT=10,
   MODEL_FAMILY_TRANSFORMER_MOE=11
  };

enum ENUM_MAX_ONNX_POLICY_FAMILY
  {
   POLICY_FAMILY_AUTO=0,
   POLICY_FAMILY_LIGHTGBM=1,
   POLICY_FAMILY_XGBOOST=2,
   POLICY_FAMILY_RANDOM_FOREST=3
  };

struct FamilySignal
  {
   double signal;
   double quality;
  };

struct MarketSnapshot
  {
   datetime bar_time;
   double open1,high1,low1,close1,close2;
   double atr,atr_slow;
   double ma_fast,ma_fast_prev,ma_slow;
   double adx,plus_di,minus_di,rsi;
   double bb_mid,bb_upper,bb_lower;
   double ret1,ret3,ret6;
   double efficiency10;
   double volume_z;
   double highest20,lowest20;
   double body_atr,upper_wick_atr,lower_wick_atr,range_atr;
   double atr_ratio;
   double bb_z;
   double bb_width_pct;
  };

struct OnnxPrediction
  {
   bool   valid;
   double sell_prob;
   double skip_prob;
   double buy_prob;
   double directional;
   double take_prob;
  };

struct DecisionPolicy
  {
   bool   valid;
   double take_threshold;
   double buy_threshold;
   double sell_threshold;
   double directional_margin;
   double max_entropy;
   string regime_mode;
  };

enum ENUM_MTF_ROLE
  {
   MTF_ROLE_CONTEXT=0,
   MTF_ROLE_STRUCTURE=1,
   MTF_ROLE_MAIN=2,
   MTF_ROLE_TIMING=3
  };

struct RoleIndicators
  {
   int atr;
   int atr_slow;
   int ma_fast;
   int ma_slow;
   int adx;
   int rsi;
   int bands;
  };

input group "General"
input long   InpMagic                 = 26090801;
input bool   InpAllowLiveTrading      = false;
input bool   InpOneDecisionPerBar     = true;
input int    InpWarmupBars            = 120;
input int    InpSlippagePoints        = 20;

input group "Risk / Execution"
input double InpRiskPct               = 0.50;
input double InpSL_ATR                = 3.2;
input double InpTP_ATR                = 4.8;
input int    InpMaxHoldBars           = 54;
input double InpMaxSpreadPoints       = 45.0;
input double InpMaxDailyLossPct       = 3.0;
input double InpShockHaltATR          = 3.5;
input double InpEntryThreshold        = 0.18;
input double InpExitReverseThreshold  = 0.25;
input double InpMinConsensus          = 0.7;

input group "Strategy Family Weights"
input double InpWeightTrend           = 1.7;
input double InpWeightRange           = 1.7;
input double InpWeightBreakout        = 0.2;
input double InpWeightPullback        = 1.5;
input double InpWeightSession         = 0.3;
input double InpWeightShock           = 1.5;
input double InpWeightRelative        = 0.8;

input group "Session Family (broker server time)"
input int    InpAsiaStartHour         = 0;
input int    InpLondonStartHour       = 7;
input int    InpNewYorkStartHour      = 13;
input int    InpSessionEndHour        = 21;

input group "Relative Value Family"
input string InpConfirmSymbol         = "";
input int    InpRelativeLookback      = 60;
input double InpMinRelativeCorr       = 0.25;

input group "True MTF Strategy Geometry (frozen by Optimizer)"
input string InpStrategyContract      = "MAX_TRUE_MTF_DYNAMIC_V1";
input string InpMTFResolverVersion    = "TRUE_MTF_LOG_RATIO_V1";
input int    InpMTFContextMinutes     = 720;
input int    InpMTFStructureMinutes   = 240;
input int    InpMTFMainMinutes        = 60;
input int    InpMTFTimingMinutes      = 20;

input group "ONNX Champion"
input bool   InpUseOnnxChampion       = false;
input ENUM_MAX_ONNX_MODEL_FAMILY InpChampionModelFamily = MODEL_FAMILY_AUTO;
input bool   InpChampionHybrid         = false; // generic temporal model -> classical tree policy
input string InpChampionModel         = "models\\champion.onnx";
input int    InpChampionSequenceLength = 1; // exact exported input sequence; tabular models normally use 1
input string InpChampionTemporalModel = "models\\champion_temporal.onnx";
input ENUM_MAX_ONNX_POLICY_FAMILY InpChampionHybridPolicyFamily = POLICY_FAMILY_AUTO;
input string InpChampionHybridPolicyModel = "models\\champion_policy_model.onnx";
input int    InpChampionHybridSequenceLength = 12;
input double InpOnnxBlend             = 0.65;
input double InpMinOnnxTakeProb       = 0.52;
input bool   InpRequireOnnxAgreement  = false;
input bool   InpOnnxFailClosed        = true;
input bool   InpOnnxDebugLogs         = false;
input bool   InpUseChampionPolicy      = true;
input string InpChampionPolicyFile     = "models\\champion_policy.csv";
input bool   InpRequireChampionPolicy  = false;

input group "ONNX Challenger (SHADOW ONLY)"
input bool   InpUseOnnxChallenger     = false;
input ENUM_MAX_ONNX_MODEL_FAMILY InpChallengerModelFamily = MODEL_FAMILY_AUTO;
input bool   InpChallengerHybrid       = false; // shadow generic temporal model -> classical tree policy
input string InpChallengerModel       = "models\\challenger.onnx";
input int    InpChallengerSequenceLength= 1;
input string InpChallengerTemporalModel = "models\\challenger_temporal.onnx";
input ENUM_MAX_ONNX_POLICY_FAMILY InpChallengerHybridPolicyFamily = POLICY_FAMILY_AUTO;
input string InpChallengerHybridPolicyModel = "models\\challenger_policy_model.onnx";
input int    InpChallengerHybridSequenceLength = 12;
input bool   InpUseChallengerPolicy    = true;
input string InpChallengerPolicyFile  = "models\\challenger_policy.csv";
input bool   InpRequireChallengerPolicy= true;

input group "Telemetry"
input bool   InpWriteTelemetry        = true;
input string InpTelemetryFile         = "Max_MTF_Telemetry.csv";
input bool   InpWriteTrainingData      = true;
input string InpTrainingFile           = "Max_MTF_Training.csv";

input group "Trade Audit CSV"
input bool   InpWriteChampionTrades   = true;
input string InpChampionTradesFile    = "Max_MTF_Champion_Trades.csv";
input bool   InpWriteShadowTrades     = true;
input string InpShadowTradesFile      = "Max_MTF_Shadow_Trades.csv";

input group "Optimizer Evidence"
input double InpOptimizerTradeExponent = 0.50;
input string InpOptimizerMetricsFile   = "Max_MTF_metrics.csv";
input long   InpOptimizerRunNonce      = 0;

CTrade g_trade;

RoleIndicators g_roleIndicators[MTF_ROLE_COUNT];
ENUM_TIMEFRAMES g_roleTf[MTF_ROLE_COUNT];
int g_roleMinutes[MTF_ROLE_COUNT];

long g_onnxChampion=INVALID_HANDLE;
long g_onnxChallenger=INVALID_HANDLE;
long g_onnxChampionTemporal=INVALID_HANDLE;
long g_onnxChampionHybridPolicy=INVALID_HANDLE;
long g_onnxChallengerTemporal=INVALID_HANDLE;
long g_onnxChallengerHybridPolicy=INVALID_HANDLE;
DecisionPolicy g_championPolicy;
DecisionPolicy g_challengerPolicy;

datetime g_lastBarTime=0;
int      g_dayKey=-1;
double   g_dayStartEquity=0.0;
int      g_csv=INVALID_HANDLE;
int      g_championTradesCsv=INVALID_HANDLE;
int      g_shadowTradesCsv=INVALID_HANDLE;
ulong    g_championPositionIds[];
datetime g_lastTrainingSignalTime=0;
datetime g_trainingTimes[];
ulong    g_trainingKnownSize=0;
bool     g_trainingIndexReady=false;
int      g_featureHistoryCount=0;
int      g_featureHistoryCapacity=1;
double   g_featureHistory[];

// Strategy Optimizer tester-only R accounting.
// v0.8.6 rebuilds R from complete tester history inside OnTester(). This is
// intentionally independent from OnTradeTransaction event ordering so SL/TP,
// tester-end liquidation, and queued trade events cannot poison every pass.
struct OptimizerPositionLedger
  {
   ulong  position_id;
   double sum_net;
   double sum_initial_risk;
   int    entry_deals;
   int    exit_deals;
  };

double g_optimizerSumR=0.0;
double g_optimizerSumNet=0.0;
double g_optimizerSumRisk=0.0;
int    g_optimizerClosedTrades=0;
int    g_optimizerAccountingErrors=0;
int    g_optimizerMetricsHandle=INVALID_HANDLE;

bool StrategyOptimizerSevenFamilyContract()
  {
   if(!MQLInfoInteger(MQL_OPTIMIZATION)) return true;
   if(!InpAllowLiveTrading) return false;
   if(StringLen(InpConfirmSymbol)==0 || InpConfirmSymbol==_Symbol) return false;
   if(!SymbolSelect(InpConfirmSymbol,true)) return false;
   if(InpWeightTrend<=0.0 || InpWeightRange<=0.0 || InpWeightBreakout<=0.0 ||
      InpWeightPullback<=0.0 || InpWeightSession<=0.0 || InpWeightShock<=0.0 ||
      InpWeightRelative<=0.0) return false;
   return true;
  }

int StrategyOptimizerLedgerIndex(OptimizerPositionLedger &rows[],const ulong position_id)
  {
   int n=ArraySize(rows);
   for(int i=0;i<n;i++)
      if(rows[i].position_id==position_id) return i;
   return -1;
  }

bool StrategyOptimizerInitialRiskFromDeal(const ulong deal,double &risk_money)
  {
   risk_money=0.0;
   if(deal==0) return false;
   long entry=(long)HistoryDealGetInteger(deal,DEAL_ENTRY);
   if(entry!=DEAL_ENTRY_IN) return false;
   long deal_type=(long)HistoryDealGetInteger(deal,DEAL_TYPE);
   ENUM_ORDER_TYPE action;
   if(deal_type==DEAL_TYPE_BUY) action=ORDER_TYPE_BUY;
   else if(deal_type==DEAL_TYPE_SELL) action=ORDER_TYPE_SELL;
   else return false;

   double volume=HistoryDealGetDouble(deal,DEAL_VOLUME);
   double entry_price=HistoryDealGetDouble(deal,DEAL_PRICE);
   double stop_price=0.0;
   ulong order_ticket=(ulong)HistoryDealGetInteger(deal,DEAL_ORDER);
   if(order_ticket>0)
      stop_price=HistoryOrderGetDouble(order_ticket,ORDER_SL);
   if(stop_price<=0.0)
      stop_price=HistoryDealGetDouble(deal,DEAL_SL);
   if(volume<=0.0 || entry_price<=0.0 || stop_price<=0.0) return false;
   if(action==ORDER_TYPE_BUY && stop_price>=entry_price) return false;
   if(action==ORDER_TYPE_SELL && stop_price<=entry_price) return false;

   double stop_pnl=0.0;
   ResetLastError();
   if(!OrderCalcProfit(action,_Symbol,volume,entry_price,stop_price,stop_pnl)) return false;
   risk_money=MathAbs(stop_pnl);
   return MathIsValidNumber(risk_money) && risk_money>0.0;
  }

bool StrategyOptimizerRebuildHistoryMetrics()
  {
   g_optimizerSumR=0.0;
   g_optimizerSumNet=0.0;
   g_optimizerSumRisk=0.0;
   g_optimizerClosedTrades=0;
   g_optimizerAccountingErrors=0;

   datetime to_time=TimeCurrent();
   if(to_time<=0) to_time=D'2099.12.31 23:59';
   else to_time+=86400;
   if(!HistorySelect(0,to_time))
     {
      g_optimizerAccountingErrors++;
      return false;
     }

   OptimizerPositionLedger ledgers[];
   int total=HistoryDealsTotal();

   // Pass 1: create ledgers only from this EA's entry deals and reconstruct the
   // exact initial stop risk from actual fill + entry-order SL. Partial fills are
   // additive, so one position can safely contain more than one entry deal.
   for(int i=0;i<total;i++)
     {
      ulong deal=HistoryDealGetTicket(i);
      if(deal==0) continue;
      if(HistoryDealGetString(deal,DEAL_SYMBOL)!=_Symbol) continue;
      if((long)HistoryDealGetInteger(deal,DEAL_ENTRY)!=DEAL_ENTRY_IN) continue;
      if((long)HistoryDealGetInteger(deal,DEAL_MAGIC)!=InpMagic) continue;
      ulong position_id=(ulong)HistoryDealGetInteger(deal,DEAL_POSITION_ID);
      if(position_id==0) { g_optimizerAccountingErrors++; continue; }
      int idx=StrategyOptimizerLedgerIndex(ledgers,position_id);
      if(idx<0)
        {
         idx=ArraySize(ledgers);
         if(ArrayResize(ledgers,idx+1)!=idx+1) { g_optimizerAccountingErrors++; continue; }
         ledgers[idx].position_id=position_id;
         ledgers[idx].sum_net=0.0;
         ledgers[idx].sum_initial_risk=0.0;
         ledgers[idx].entry_deals=0;
         ledgers[idx].exit_deals=0;
        }
      double risk_money=0.0;
      if(!StrategyOptimizerInitialRiskFromDeal(deal,risk_money))
        {
         g_optimizerAccountingErrors++;
         continue;
        }
      ledgers[idx].sum_initial_risk+=risk_money;
      ledgers[idx].entry_deals++;
     }

   // Pass 2: sum every deal belonging to those positions. Exit deals may be
   // generated by broker/tester SL/TP mechanics, so position identity is the
   // authority after the entry deal; requiring exit DEAL_MAGIC would be brittle.
   for(int i=0;i<total;i++)
     {
      ulong deal=HistoryDealGetTicket(i);
      if(deal==0) continue;
      if(HistoryDealGetString(deal,DEAL_SYMBOL)!=_Symbol) continue;
      ulong position_id=(ulong)HistoryDealGetInteger(deal,DEAL_POSITION_ID);
      int idx=StrategyOptimizerLedgerIndex(ledgers,position_id);
      if(idx<0) continue;
      ledgers[idx].sum_net+=HistoryDealGetDouble(deal,DEAL_PROFIT);
      ledgers[idx].sum_net+=HistoryDealGetDouble(deal,DEAL_COMMISSION);
      ledgers[idx].sum_net+=HistoryDealGetDouble(deal,DEAL_SWAP);
      ledgers[idx].sum_net+=HistoryDealGetDouble(deal,DEAL_FEE);
      long entry=(long)HistoryDealGetInteger(deal,DEAL_ENTRY);
      if(entry==DEAL_ENTRY_OUT || entry==DEAL_ENTRY_OUT_BY)
         ledgers[idx].exit_deals++;
      else if(entry==DEAL_ENTRY_INOUT)
         g_optimizerAccountingErrors++;
     }

   int n=ArraySize(ledgers);
   for(int i=0;i<n;i++)
     {
      if(ledgers[i].entry_deals<=0 || ledgers[i].exit_deals<=0 ||
         ledgers[i].sum_initial_risk<=0.0 || !MathIsValidNumber(ledgers[i].sum_net))
        {
         g_optimizerAccountingErrors++;
         continue;
        }
      double r=ledgers[i].sum_net/ledgers[i].sum_initial_risk;
      if(!MathIsValidNumber(r))
        {
         g_optimizerAccountingErrors++;
         continue;
        }
      g_optimizerSumR+=r;
      g_optimizerSumNet+=ledgers[i].sum_net;
      g_optimizerSumRisk+=ledgers[i].sum_initial_risk;
      g_optimizerClosedTrades++;
     }
   return g_optimizerAccountingErrors==0;
  }

double Clamp(const double v,const double lo,const double hi)
  {
   if(v<lo) return lo;
   if(v>hi) return hi;
   return v;
  }

double Clamp01(const double v) { return Clamp(v,0.0,1.0); }

double SafeDiv(const double a,const double b,const double fallback=0.0)
  {
   if(MathAbs(b)<1e-12) return fallback;
   return a/b;
  }

double Sign(const double x)
  {
   if(x>0.0) return 1.0;
   if(x<0.0) return -1.0;
   return 0.0;
  }

double WeightForFamily(const int family)
  {
   switch(family)
     {
      case FAMILY_TREND:    return MathMax(0.0,InpWeightTrend);
      case FAMILY_RANGE:    return MathMax(0.0,InpWeightRange);
      case FAMILY_BREAKOUT: return MathMax(0.0,InpWeightBreakout);
      case FAMILY_PULLBACK: return MathMax(0.0,InpWeightPullback);
      case FAMILY_SESSION:  return MathMax(0.0,InpWeightSession);
      case FAMILY_SHOCK:    return MathMax(0.0,InpWeightShock);
      case FAMILY_RELATIVE: return MathMax(0.0,InpWeightRelative);
     }
   return 0.0;
  }

ENUM_TIMEFRAMES TimeframeFromMinutes(const int minutes)
  {
   switch(minutes)
     {
      case 1: return PERIOD_M1;
      case 2: return PERIOD_M2;
      case 3: return PERIOD_M3;
      case 4: return PERIOD_M4;
      case 5: return PERIOD_M5;
      case 6: return PERIOD_M6;
      case 10: return PERIOD_M10;
      case 12: return PERIOD_M12;
      case 15: return PERIOD_M15;
      case 20: return PERIOD_M20;
      case 30: return PERIOD_M30;
      case 60: return PERIOD_H1;
      case 120: return PERIOD_H2;
      case 180: return PERIOD_H3;
      case 240: return PERIOD_H4;
      case 360: return PERIOD_H6;
      case 480: return PERIOD_H8;
      case 720: return PERIOD_H12;
      case 1440: return PERIOD_D1;
      case 10080: return PERIOD_W1;
      case 43200: return PERIOD_MN1;
     }
   return PERIOD_CURRENT;
  }

int ClosestSupportedMtfMinutes(const double target,const int min_inclusive,const int min_exclusive,const int max_exclusive)
  {
   int supported[21]={1,2,3,4,5,6,10,12,15,20,30,60,120,180,240,360,480,720,1440,10080,43200};
   double best=DBL_MAX;
   int best_minutes=-1;
   for(int i=0;i<ArraySize(supported);i++)
     {
      int m=supported[i];
      if(min_inclusive>0 && m<min_inclusive) continue;
      if(min_exclusive>0 && m<=min_exclusive) continue;
      if(max_exclusive>0 && m>=max_exclusive) continue;
      double distance=MathAbs(MathLog((double)m/target));
      if(distance<best-1.0e-12 || (MathAbs(distance-best)<=1.0e-12 && (best_minutes<0 || m<best_minutes)))
        {
         best=distance;
         best_minutes=m;
        }
     }
   return best_minutes;
  }

bool ResolveTrueMtfMinutes(const int main_minutes,int &context_minutes,int &structure_minutes,int &timing_minutes)
  {
   context_minutes=-1;
   structure_minutes=-1;
   timing_minutes=-1;
   if(main_minutes<15 || TimeframeFromMinutes(main_minutes)==PERIOD_CURRENT) return false;
   timing_minutes=ClosestSupportedMtfMinutes((double)main_minutes/3.0,5,0,main_minutes);
   structure_minutes=ClosestSupportedMtfMinutes((double)main_minutes*4.0,0,main_minutes,0);
   if(timing_minutes<0 || structure_minutes<0) return false;
   context_minutes=ClosestSupportedMtfMinutes((double)main_minutes*16.0,0,structure_minutes,0);
   if(context_minutes<0) return false;
   return (5<=timing_minutes && timing_minutes<main_minutes &&
           main_minutes<structure_minutes && structure_minutes<context_minutes);
  }

bool ValidateTrueMtfContract()
  {
   if(InpStrategyContract!=MAX_MTF_STRATEGY_CONTRACT)
     {
      Print("MTF strategy contract mismatch: ",InpStrategyContract);
      return false;
     }
   if(InpMTFResolverVersion!=MAX_MTF_RESOLVER_VERSION)
     {
      Print("MTF resolver version mismatch: ",InpMTFResolverVersion);
      return false;
     }
   int chart_minutes=PeriodSeconds(_Period)/60;
   if(chart_minutes!=InpMTFMainMinutes || chart_minutes<15)
     {
      Print("MTF main timeframe mismatch/below M15: chart=",chart_minutes," frozen=",InpMTFMainMinutes);
      return false;
     }
   int expected_context=0,expected_structure=0,expected_timing=0;
   if(!ResolveTrueMtfMinutes(chart_minutes,expected_context,expected_structure,expected_timing))
     {
      Print("MTF geometry cannot resolve four distinct roles for main minutes ",chart_minutes);
      return false;
     }
   if(expected_context!=InpMTFContextMinutes ||
      expected_structure!=InpMTFStructureMinutes ||
      expected_timing!=InpMTFTimingMinutes)
     {
      PrintFormat("MTF frozen/backend parity mismatch expected=%d/%d/%d/%d supplied=%d/%d/%d/%d",
                  expected_context,expected_structure,chart_minutes,expected_timing,
                  InpMTFContextMinutes,InpMTFStructureMinutes,InpMTFMainMinutes,InpMTFTimingMinutes);
      return false;
     }
   if(!(5<=InpMTFTimingMinutes && InpMTFTimingMinutes<InpMTFMainMinutes &&
        InpMTFMainMinutes<InpMTFStructureMinutes && InpMTFStructureMinutes<InpMTFContextMinutes))
      return false;

   g_roleMinutes[MTF_ROLE_CONTEXT]=InpMTFContextMinutes;
   g_roleMinutes[MTF_ROLE_STRUCTURE]=InpMTFStructureMinutes;
   g_roleMinutes[MTF_ROLE_MAIN]=InpMTFMainMinutes;
   g_roleMinutes[MTF_ROLE_TIMING]=InpMTFTimingMinutes;
   for(int role=0;role<MTF_ROLE_COUNT;role++)
     {
      g_roleTf[role]=TimeframeFromMinutes(g_roleMinutes[role]);
      if(g_roleTf[role]==PERIOD_CURRENT) return false;
     }
   return true;
  }

void ResetRoleIndicators(const int role)
  {
   g_roleIndicators[role].atr=INVALID_HANDLE;
   g_roleIndicators[role].atr_slow=INVALID_HANDLE;
   g_roleIndicators[role].ma_fast=INVALID_HANDLE;
   g_roleIndicators[role].ma_slow=INVALID_HANDLE;
   g_roleIndicators[role].adx=INVALID_HANDLE;
   g_roleIndicators[role].rsi=INVALID_HANDLE;
   g_roleIndicators[role].bands=INVALID_HANDLE;
  }

bool InitRoleIndicators(const int role)
  {
   if(role<0 || role>=MTF_ROLE_COUNT) return false;
   ResetRoleIndicators(role);
   ENUM_TIMEFRAMES tf=g_roleTf[role];
   g_roleIndicators[role].atr=iATR(_Symbol,tf,14);
   g_roleIndicators[role].atr_slow=iATR(_Symbol,tf,50);
   g_roleIndicators[role].ma_fast=iMA(_Symbol,tf,20,0,MODE_EMA,PRICE_CLOSE);
   g_roleIndicators[role].ma_slow=iMA(_Symbol,tf,50,0,MODE_EMA,PRICE_CLOSE);
   g_roleIndicators[role].adx=iADX(_Symbol,tf,14);
   g_roleIndicators[role].rsi=iRSI(_Symbol,tf,14,PRICE_CLOSE);
   g_roleIndicators[role].bands=iBands(_Symbol,tf,20,0,2.0,PRICE_CLOSE);
   return (g_roleIndicators[role].atr!=INVALID_HANDLE &&
           g_roleIndicators[role].atr_slow!=INVALID_HANDLE &&
           g_roleIndicators[role].ma_fast!=INVALID_HANDLE &&
           g_roleIndicators[role].ma_slow!=INVALID_HANDLE &&
           g_roleIndicators[role].adx!=INVALID_HANDLE &&
           g_roleIndicators[role].rsi!=INVALID_HANDLE &&
           g_roleIndicators[role].bands!=INVALID_HANDLE);
  }

void ReleaseRoleIndicators(const int role)
  {
   if(role<0 || role>=MTF_ROLE_COUNT) return;
   if(g_roleIndicators[role].atr!=INVALID_HANDLE) IndicatorRelease(g_roleIndicators[role].atr);
   if(g_roleIndicators[role].atr_slow!=INVALID_HANDLE) IndicatorRelease(g_roleIndicators[role].atr_slow);
   if(g_roleIndicators[role].ma_fast!=INVALID_HANDLE) IndicatorRelease(g_roleIndicators[role].ma_fast);
   if(g_roleIndicators[role].ma_slow!=INVALID_HANDLE) IndicatorRelease(g_roleIndicators[role].ma_slow);
   if(g_roleIndicators[role].adx!=INVALID_HANDLE) IndicatorRelease(g_roleIndicators[role].adx);
   if(g_roleIndicators[role].rsi!=INVALID_HANDLE) IndicatorRelease(g_roleIndicators[role].rsi);
   if(g_roleIndicators[role].bands!=INVALID_HANDLE) IndicatorRelease(g_roleIndicators[role].bands);
   ResetRoleIndicators(role);
  }

datetime BarCloseTime(const string symbol,const ENUM_TIMEFRAMES tf,const int shift)
  {
   // MT5 monthly bars are calendar-length, not a fixed PeriodSeconds(MN1)
   // duration. For every historical bar, the next newer bar open is the exact
   // causal close boundary. Shift 0 is still open by definition.
   if(shift<=0) return 0;
   return iTime(symbol,tf,shift-1);
  }

int LatestFullyClosedBarShift(const string symbol,const ENUM_TIMEFRAMES tf,const datetime decision_time)
  {
   if(decision_time<=0) return -1;
   int shift=iBarShift(symbol,tf,decision_time,false);
   if(shift<0) return -1;
   for(int guard=0;guard<10000;guard++)
     {
      datetime open_time=iTime(symbol,tf,shift);
      if(open_time<=0) return -1;
      datetime close_time=BarCloseTime(symbol,tf,shift);
      if(close_time>0 && close_time<=decision_time) return shift;
      shift++;
     }
   return -1;
  }

bool CopyOne(const int handle,const int buffer,const int shift,double &value)
  {
   double tmp[1];
   ResetLastError();
   if(CopyBuffer(handle,buffer,shift,1,tmp)!=1) return false;
   value=tmp[0];
   return MathIsValidNumber(value);
  }

int VolumeDigits()
  {
   double step=SymbolInfoDouble(_Symbol,SYMBOL_VOLUME_STEP);
   if(step<=0.0) return 2;
   int d=0;
   while(step<1.0 && d<8) { step*=10.0; d++; }
   return d;
  }

bool IsNewBar()
  {
   datetime t=iTime(_Symbol,g_roleTf[MTF_ROLE_MAIN],0);
   if(t<=0) return false;
   if(g_lastBarTime==0) { g_lastBarTime=t; return false; }
   if(t!=g_lastBarTime) { g_lastBarTime=t; return true; }
   return false;
  }

bool IsTreeModelFamily(const ENUM_MAX_ONNX_MODEL_FAMILY family)
  {
   return family==MODEL_FAMILY_LIGHTGBM || family==MODEL_FAMILY_XGBOOST || family==MODEL_FAMILY_RANDOM_FOREST;
  }

bool IsTemporalModelFamily(const ENUM_MAX_ONNX_MODEL_FAMILY family)
  {
   return family==MODEL_FAMILY_GRU || family==MODEL_FAMILY_LSTM || family==MODEL_FAMILY_TCN ||
          family==MODEL_FAMILY_TRANSFORMER_ENCODER || family==MODEL_FAMILY_PATCHTST ||
          family==MODEL_FAMILY_ITRANSFORMER || family==MODEL_FAMILY_TFT || family==MODEL_FAMILY_TRANSFORMER_MOE;
  }

bool ValidateOnnxTopologyIdentity(const bool hybrid,const ENUM_MAX_ONNX_MODEL_FAMILY family,const ENUM_MAX_ONNX_POLICY_FAMILY policy_family,const string role)
  {
   if(!hybrid) return true;
   if(family!=MODEL_FAMILY_AUTO && !IsTemporalModelFamily(family))
     {
      Print(role," hybrid requires a temporal Model Family; got ",EnumToString(family));
      return false;
     }
   if(policy_family<POLICY_FAMILY_AUTO || policy_family>POLICY_FAMILY_RANDOM_FOREST)
     {
      Print(role," hybrid policy family is invalid");
      return false;
     }
   return true;
  }

void UpdateDailyState()
  {
   MqlDateTime dt;
   TimeToStruct(TimeCurrent(),dt);
   int key=dt.year*10000+dt.mon*100+dt.day;
   if(key!=g_dayKey)
     {
      g_dayKey=key;
      g_dayStartEquity=AccountInfoDouble(ACCOUNT_EQUITY);
     }
  }

double DailyLossPct()
  {
   if(g_dayStartEquity<=0.0) return 0.0;
   double eq=AccountInfoDouble(ACCOUNT_EQUITY);
   return MathMax(0.0,(g_dayStartEquity-eq)/g_dayStartEquity*100.0);
  }

double CurrentSpreadPoints()
  {
   MqlTick tick;
   if(!SymbolInfoTick(_Symbol,tick)) return DBL_MAX;
   double point=SymbolInfoDouble(_Symbol,SYMBOL_POINT);
   if(point<=0.0) return DBL_MAX;
   return (tick.ask-tick.bid)/point;
  }

bool GetOwnPosition(ulong &ticket,long &type,datetime &open_time)
  {
   ticket=0; type=-1; open_time=0;
   for(int i=PositionsTotal()-1;i>=0;i--)
     {
      ulong t=PositionGetTicket(i);
      if(t==0) continue;
      if(PositionGetString(POSITION_SYMBOL)==_Symbol &&
         (long)PositionGetInteger(POSITION_MAGIC)==InpMagic)
        {
         ticket=t;
         type=(long)PositionGetInteger(POSITION_TYPE);
         open_time=(datetime)PositionGetInteger(POSITION_TIME);
         return true;
        }
     }
   return false;
  }

bool AnyPositionOnSymbol()
  {
   for(int i=PositionsTotal()-1;i>=0;i--)
     {
      ulong t=PositionGetTicket(i);
      if(t==0) continue;
      if(PositionGetString(POSITION_SYMBOL)==_Symbol) return true;
     }
   return false;
  }

int BarsHeld(const datetime open_time)
  {
   int sec=PeriodSeconds(g_roleTf[MTF_ROLE_MAIN]);
   if(sec<=0 || open_time<=0) return 0;
   return (int)MathMax(0,(TimeCurrent()-open_time)/sec);
  }

bool BuildRoleSnapshot(const int role,const datetime decision_time,MarketSnapshot &s,MqlRates &rates[])
  {
   if(role<0 || role>=MTF_ROLE_COUNT) return false;
   ENUM_TIMEFRAMES tf=g_roleTf[role];
   int shift=LatestFullyClosedBarShift(_Symbol,tf,decision_time);
   if(shift<0) return false;

   int need=MathMax(InpWarmupBars,80);
   ArraySetAsSeries(rates,true);
   if(CopyRates(_Symbol,tf,shift,need,rates)<need) return false;
   datetime close_time=BarCloseTime(_Symbol,tf,shift);
   if(close_time<=0 || close_time>decision_time) return false;

   s.bar_time=rates[0].time;
   s.open1=rates[0].open; s.high1=rates[0].high; s.low1=rates[0].low;
   s.close1=rates[0].close; s.close2=rates[1].close;

   if(!CopyOne(g_roleIndicators[role].atr,0,shift,s.atr)) return false;
   if(!CopyOne(g_roleIndicators[role].atr_slow,0,shift,s.atr_slow)) return false;
   if(!CopyOne(g_roleIndicators[role].ma_fast,0,shift,s.ma_fast)) return false;
   if(!CopyOne(g_roleIndicators[role].ma_fast,0,shift+1,s.ma_fast_prev)) return false;
   if(!CopyOne(g_roleIndicators[role].ma_slow,0,shift,s.ma_slow)) return false;
   if(!CopyOne(g_roleIndicators[role].adx,0,shift,s.adx)) return false;
   if(!CopyOne(g_roleIndicators[role].adx,1,shift,s.plus_di)) return false;
   if(!CopyOne(g_roleIndicators[role].adx,2,shift,s.minus_di)) return false;
   if(!CopyOne(g_roleIndicators[role].rsi,0,shift,s.rsi)) return false;
   if(!CopyOne(g_roleIndicators[role].bands,0,shift,s.bb_mid)) return false;
   if(!CopyOne(g_roleIndicators[role].bands,1,shift,s.bb_upper)) return false;
   if(!CopyOne(g_roleIndicators[role].bands,2,shift,s.bb_lower)) return false;
   if(s.atr<=0.0 || s.close1<=0.0) return false;

   s.ret1=s.close1/rates[1].close-1.0;
   s.ret3=s.close1/rates[3].close-1.0;
   s.ret6=s.close1/rates[6].close-1.0;

   double path=0.0;
   for(int i=0;i<10;i++) path+=MathAbs(rates[i].close-rates[i+1].close);
   s.efficiency10=SafeDiv(MathAbs(rates[0].close-rates[10].close),path,0.0);

   s.highest20=-DBL_MAX; s.lowest20=DBL_MAX;
   for(int i=1;i<=20;i++)
     {
      if(rates[i].high>s.highest20) s.highest20=rates[i].high;
      if(rates[i].low<s.lowest20) s.lowest20=rates[i].low;
     }

   double body=MathAbs(s.close1-s.open1);
   double range=s.high1-s.low1;
   double upper=s.high1-MathMax(s.open1,s.close1);
   double lower=MathMin(s.open1,s.close1)-s.low1;
   s.body_atr=SafeDiv(body,s.atr,0.0);
   s.upper_wick_atr=SafeDiv(MathMax(0.0,upper),s.atr,0.0);
   s.lower_wick_atr=SafeDiv(MathMax(0.0,lower),s.atr,0.0);
   s.range_atr=SafeDiv(MathMax(0.0,range),s.atr,0.0);
   s.atr_ratio=SafeDiv(s.atr,s.atr_slow,1.0);

   double half_width=(s.bb_upper-s.bb_lower)/2.0;
   s.bb_z=SafeDiv(s.close1-s.bb_mid,half_width,0.0);
   s.bb_width_pct=SafeDiv(s.bb_upper-s.bb_lower,s.close1,0.0)*100.0;

   int vn=30;
   double mean_vol=0.0;
   for(int i=1;i<=vn;i++) mean_vol+=(double)rates[i].tick_volume;
   mean_vol/=vn;
   double var=0.0;
   for(int i=1;i<=vn;i++)
     {
      double d=(double)rates[i].tick_volume-mean_vol;
      var+=d*d;
     }
   double stdv=MathSqrt(var/MathMax(1,vn-1));
   s.volume_z=SafeDiv((double)rates[0].tick_volume-mean_vol,stdv,0.0);
   return true;
  }

FamilySignal MakeSignal(const double signal,const double quality)
  {
   FamilySignal f;
   f.signal=Clamp(signal,-1.0,1.0);
   f.quality=Clamp01(quality);
   return f;
  }

FamilySignal SignalTrend(const MarketSnapshot &s)
  {
   double dir=Sign(s.ma_fast-s.ma_slow);
   double separation=MathAbs(s.ma_fast-s.ma_slow)/s.atr;
   double adx_q=Clamp01((s.adx-18.0)/22.0);
   double eff_q=Clamp01((s.efficiency10-0.20)/0.45);
   double di_q=Clamp01(MathAbs(s.plus_di-s.minus_di)/35.0);
   double q=0.45*adx_q+0.30*eff_q+0.25*di_q;
   return MakeSignal(dir*MathMin(1.0,separation/1.5),q);
  }

FamilySignal SignalRange(const MarketSnapshot &s)
  {
   double range_q=Clamp01((28.0-s.adx)/16.0);
   double extreme=Clamp(s.bb_z,-1.5,1.5)/1.5;
   double signal=-extreme;
   double q=range_q*Clamp01((MathAbs(s.bb_z)-0.25)/0.75);
   if(s.atr_ratio>1.45) q*=0.35;
   return MakeSignal(signal,q);
  }

FamilySignal SignalBreakout(const MarketSnapshot &s)
  {
   double signal=0.0;
   if(s.close1>s.highest20) signal=1.0;
   else if(s.close1<s.lowest20) signal=-1.0;
   else
     {
      double top_dist=(s.highest20-s.close1)/s.atr;
      double bot_dist=(s.close1-s.lowest20)/s.atr;
      if(top_dist>=0.0 && top_dist<0.20) signal=0.35;
      if(bot_dist>=0.0 && bot_dist<0.20) signal=-0.35;
     }
   double vol_q=Clamp01((s.atr_ratio-0.90)/0.70);
   double adx_q=Clamp01((s.adx-16.0)/24.0);
   double body_q=Clamp01(s.body_atr/1.20);
   double q=(0.40*vol_q+0.35*adx_q+0.25*body_q)*MathMin(1.0,MathAbs(signal)+0.25);
   return MakeSignal(signal,q);
  }

FamilySignal SignalPullback(const MarketSnapshot &s)
  {
   double trend_dir=Sign(s.ma_fast-s.ma_slow);
   double adx_q=Clamp01((s.adx-18.0)/22.0);
   double signal=0.0,q=0.0;
   if(trend_dir>0.0 && s.close1<s.ma_fast && s.close1>s.ma_slow)
     {
      double depth=(s.ma_fast-s.close1)/s.atr;
      signal=Clamp(0.40+depth/1.5,0.0,1.0);
      q=adx_q*Clamp01((depth+0.15)/0.90);
     }
   else if(trend_dir<0.0 && s.close1>s.ma_fast && s.close1<s.ma_slow)
     {
      double depth=(s.close1-s.ma_fast)/s.atr;
      signal=-Clamp(0.40+depth/1.5,0.0,1.0);
      q=adx_q*Clamp01((depth+0.15)/0.90);
     }
   return MakeSignal(signal,q);
  }

FamilySignal SignalSession(const MarketSnapshot &s)
  {
   MqlDateTime dt;
   TimeToStruct(s.bar_time,dt);
   int h=dt.hour;
   double signal=0.0,q=0.0;
   if(h>=InpAsiaStartHour && h<InpLondonStartHour)
     {
      signal=-Clamp(s.bb_z/1.5,-1.0,1.0);
      q=0.30*Clamp01((28.0-s.adx)/18.0);
     }
   else if(h>=InpLondonStartHour && h<InpNewYorkStartHour)
     {
      signal=Clamp(SafeDiv(s.ret3,s.atr/s.close1,0.0)/3.0,-1.0,1.0);
      q=0.45*Clamp01((s.adx-15.0)/25.0+0.25);
     }
   else if(h>=InpNewYorkStartHour && h<InpSessionEndHour)
     {
      signal=Clamp(SafeDiv(s.ret3,s.atr/s.close1,0.0)/3.0,-1.0,1.0);
      q=0.40*Clamp01((s.adx-15.0)/25.0+0.25);
      if(s.range_atr>2.5) q*=0.45;
     }
   return MakeSignal(signal,q);
  }

FamilySignal SignalShock(const MarketSnapshot &s)
  {
   double candle_dir=Sign(s.close1-s.open1);
   double shock=Clamp01((s.range_atr-1.35)/1.75);
   double body_ratio=SafeDiv(MathAbs(s.close1-s.open1),s.high1-s.low1,0.0);
   double vol_q=Clamp01((s.atr_ratio-1.0)/0.65);
   double signal=candle_dir*shock;
   double q=shock*(0.55*Clamp01(body_ratio/0.70)+0.45*vol_q);
   if(s.range_atr>=InpShockHaltATR) { signal=0.0; q=0.0; }
   return MakeSignal(signal,q);
  }

double StdArray(const double &a[],const int n)
  {
   if(n<2) return 0.0;
   double m=0.0;
   for(int i=0;i<n;i++) m+=a[i];
   m/=n;
   double v=0.0;
   for(int i=0;i<n;i++) { double d=a[i]-m; v+=d*d; }
   return MathSqrt(v/(n-1));
  }

double CorrArray(const double &a[],const double &b[],const int n)
  {
   if(n<3) return 0.0;
   double ma=0.0,mb=0.0;
   for(int i=0;i<n;i++) { ma+=a[i]; mb+=b[i]; }
   ma/=n; mb/=n;
   double cov=0.0,va=0.0,vb=0.0;
   for(int i=0;i<n;i++)
     {
      double da=a[i]-ma,db=b[i]-mb;
      cov+=da*db; va+=da*da; vb+=db*db;
     }
   if(va<=1e-20 || vb<=1e-20) return 0.0;
   return cov/MathSqrt(va*vb);
  }

FamilySignal SignalRelative(const ENUM_TIMEFRAMES tf,const datetime decision_time)
  {
   if(StringLen(InpConfirmSymbol)==0) return MakeSignal(0.0,0.0);
   int n=MathMax(8,InpRelativeLookback);
   int shift_a=LatestFullyClosedBarShift(_Symbol,tf,decision_time);
   int shift_b=LatestFullyClosedBarShift(InpConfirmSymbol,tf,decision_time);
   if(shift_a<0 || shift_b<0) return MakeSignal(0.0,0.0);

   MqlRates a[],b[];
   ArraySetAsSeries(a,true); ArraySetAsSeries(b,true);
   if(CopyRates(_Symbol,tf,shift_a,n+1,a)<n+1) return MakeSignal(0.0,0.0);
   if(CopyRates(InpConfirmSymbol,tf,shift_b,n+1,b)<n+1) return MakeSignal(0.0,0.0);
   datetime close_a=BarCloseTime(_Symbol,tf,shift_a);
   datetime close_b=BarCloseTime(InpConfirmSymbol,tf,shift_b);
   if(close_a<=0 || close_b<=0 ||
      close_a>decision_time || close_b>decision_time)
      return MakeSignal(0.0,0.0);

   // Relative returns must be timestamp-paired across the complete n+1
   // close sequence. Checking only the n numerator bars would still allow the
   // oldest denominator to come from a different session/gap on one symbol.
   for(int i=0;i<=n;i++)
      if(a[i].time!=b[i].time) return MakeSignal(0.0,0.0);

   double ra[],rb[];
   ArrayResize(ra,n); ArrayResize(rb,n);
   double cumA=0.0,cumB=0.0;
   for(int i=0;i<n;i++)
     {
      ra[i]=SafeDiv(a[i].close,a[i+1].close,1.0)-1.0;
      rb[i]=SafeDiv(b[i].close,b[i+1].close,1.0)-1.0;
      cumA+=ra[i]; cumB+=rb[i];
     }
   double corr=CorrArray(ra,rb,n);
   if(MathAbs(corr)<InpMinRelativeCorr) return MakeSignal(0.0,0.0);
   double sa=StdArray(ra,n),sb=StdArray(rb,n),norm=MathSqrt((double)n);
   double za=SafeDiv(cumA,sa*norm,0.0),zb=SafeDiv(cumB,sb*norm,0.0);
   double divergence=za-zb;
   double signal=-Clamp(divergence/2.0,-1.0,1.0);
   if(corr<0.0) signal=-signal;
   double q=Clamp01(MathAbs(corr))*Clamp01(MathAbs(divergence)/1.5);
   return MakeSignal(signal,q);
  }

bool FamilyParticipatesOnRole(const int family,const int role)
  {
   if(family==FAMILY_SESSION)
      return (role==MTF_ROLE_MAIN || role==MTF_ROLE_TIMING);
   return true;
  }

void EvaluateFamiliesForRole(const MarketSnapshot &s,const int role,const datetime decision_time,FamilySignal &f[])
  {
   ArrayResize(f,FAMILY_COUNT);
   f[FAMILY_TREND]=SignalTrend(s);
   f[FAMILY_RANGE]=SignalRange(s);
   f[FAMILY_BREAKOUT]=SignalBreakout(s);
   f[FAMILY_PULLBACK]=SignalPullback(s);
   f[FAMILY_SESSION]=(FamilyParticipatesOnRole(FAMILY_SESSION,role) ? SignalSession(s) : MakeSignal(0.0,0.0));
   f[FAMILY_SHOCK]=SignalShock(s);
   f[FAMILY_RELATIVE]=SignalRelative(g_roleTf[role],decision_time);
  }

void FuseFamilies(const FamilySignal &context_f[],
                  const FamilySignal &structure_f[],
                  const FamilySignal &main_f[],
                  const FamilySignal &timing_f[],
                  FamilySignal &fused[])
  {
   ArrayResize(fused,FAMILY_COUNT);
   for(int family=0;family<FAMILY_COUNT;family++)
     {
      double weighted_signal=0.0;
      double weight_sum=0.0;
      int participating=0;
      if(FamilyParticipatesOnRole(family,MTF_ROLE_CONTEXT))
        {
         weighted_signal+=context_f[family].signal*context_f[family].quality;
         weight_sum+=context_f[family].quality;
         participating++;
        }
      if(FamilyParticipatesOnRole(family,MTF_ROLE_STRUCTURE))
        {
         weighted_signal+=structure_f[family].signal*structure_f[family].quality;
         weight_sum+=structure_f[family].quality;
         participating++;
        }
      if(FamilyParticipatesOnRole(family,MTF_ROLE_MAIN))
        {
         weighted_signal+=main_f[family].signal*main_f[family].quality;
         weight_sum+=main_f[family].quality;
         participating++;
        }
      if(FamilyParticipatesOnRole(family,MTF_ROLE_TIMING))
        {
         weighted_signal+=timing_f[family].signal*timing_f[family].quality;
         weight_sum+=timing_f[family].quality;
         participating++;
        }
      double signal=(weight_sum>1.0e-12 ? weighted_signal/weight_sum : 0.0);
      double quality=(participating>0 ? Clamp01(weight_sum/(double)participating) : 0.0);
      fused[family]=MakeSignal(signal,quality);
     }
  }

double RuleMetaScore(const FamilySignal &f[],double &consensus)
  {
   double signed_sum=0.0,abs_sum=0.0,total_capacity=0.0;
   for(int i=0;i<FAMILY_COUNT;i++)
     {
      double w=WeightForFamily(i);
      double c=w*f[i].quality*f[i].signal;
      signed_sum+=c;
      abs_sum+=MathAbs(c);
      total_capacity+=w*f[i].quality;
     }
   consensus=(abs_sum>1e-12 ? MathAbs(signed_sum)/abs_sum : 0.0);
   if(total_capacity<=1e-12) return 0.0;
   return Clamp(signed_sum/total_capacity,-1.0,1.0);
  }

void InitFeatureHistory()
  {
   int need=MathMax(1,MathMax(MathMax(InpChampionSequenceLength,InpChallengerSequenceLength),MathMax(InpChampionHybridSequenceLength,InpChallengerHybridSequenceLength)));
   g_featureHistoryCapacity=MathMin(ONNX_MAX_SEQUENCE,need);
   g_featureHistoryCount=0;
   ArrayResize(g_featureHistory,g_featureHistoryCapacity*ONNX_FEATURE_COUNT);
   ArrayInitialize(g_featureHistory,0.0);
  }

void PushFeatureHistory(const double &features[])
  {
   if(ArraySize(features)!=ONNX_FEATURE_COUNT) return;
   if(g_featureHistoryCapacity<=0) InitFeatureHistory();
   if(g_featureHistoryCount<g_featureHistoryCapacity)
     {
      int base=g_featureHistoryCount*ONNX_FEATURE_COUNT;
      for(int j=0;j<ONNX_FEATURE_COUNT;j++) g_featureHistory[base+j]=features[j];
      g_featureHistoryCount++;
      return;
     }
   for(int r=1;r<g_featureHistoryCapacity;r++)
     for(int j=0;j<ONNX_FEATURE_COUNT;j++)
       g_featureHistory[(r-1)*ONNX_FEATURE_COUNT+j]=g_featureHistory[r*ONNX_FEATURE_COUNT+j];
   int base=(g_featureHistoryCapacity-1)*ONNX_FEATURE_COUNT;
   for(int j=0;j<ONNX_FEATURE_COUNT;j++) g_featureHistory[base+j]=features[j];
  }

bool ValidSequenceLength(const int n)
  {
   return (n>=1 && n<=ONNX_MAX_SEQUENCE);
  }

bool InitOnnxSession(const bool enabled,const string model_file,const int sequence_length,long &handle)
  {
   handle=INVALID_HANDLE;
   if(!enabled) return true;
   if(!ValidSequenceLength(sequence_length))
     {
      Print("Invalid ONNX sequence length ",sequence_length," for ",model_file,"; allowed 1..",ONNX_MAX_SEQUENCE);
      return false;
     }
   uint flags=0;
   if(InpOnnxDebugLogs) flags|=ONNX_LOGLEVEL_INFO;
   ResetLastError();
   handle=OnnxCreate(model_file,flags);
   if(handle==INVALID_HANDLE)
     {
      Print("ONNX create failed for ",model_file,", error=",GetLastError());
      return false;
     }
   bool shape_ok=false;
   if(sequence_length<=1)
     {
      ulong input_shape2[2]={1,ONNX_FEATURE_COUNT};
      shape_ok=OnnxSetInputShape(handle,0,input_shape2);
     }
   else
     {
      ulong input_shape3[3]={1,(ulong)sequence_length,ONNX_FEATURE_COUNT};
      shape_ok=OnnxSetInputShape(handle,0,input_shape3);
     }
   ulong output_shape[2]={1,ONNX_CLASS_COUNT};
   if(!shape_ok)
     {
      Print("ONNX input shape failed for ",model_file," seq=",sequence_length,", error=",GetLastError());
      OnnxRelease(handle); handle=INVALID_HANDLE; return false;
     }
   if(!OnnxSetOutputShape(handle,0,output_shape))
     {
      Print("ONNX output shape failed for ",model_file,", error=",GetLastError());
      OnnxRelease(handle); handle=INVALID_HANDLE; return false;
     }
   if(sequence_length<=1) Print("ONNX ready: ",model_file," input=[1,",ONNX_FEATURE_COUNT,"] output=[1,",ONNX_CLASS_COUNT,"]");
   else Print("ONNX ready: ",model_file," input=[1,",sequence_length,",",ONNX_FEATURE_COUNT,"] output=[1,",ONNX_CLASS_COUNT,"]");
   return true;
  }


bool InitDirectionOnnxSession(const bool enabled,const string model_file,const int sequence_length,long &handle)
  {
   handle=INVALID_HANDLE;
   if(!enabled) return true;
   if(!ValidSequenceLength(sequence_length) || sequence_length<=1) return false;
   uint flags=0;
   if(InpOnnxDebugLogs) flags|=ONNX_LOGLEVEL_INFO;
   ResetLastError();
   handle=OnnxCreate(model_file,flags);
   if(handle==INVALID_HANDLE)
     {
      Print("Hybrid temporal ONNX create failed: ",model_file," error=",GetLastError());
      return false;
     }
   ulong input_shape[3]={1,(ulong)sequence_length,ONNX_FEATURE_COUNT};
   ulong output_shape[2]={1,DIRECTION_CLASS_COUNT};
   if(!OnnxSetInputShape(handle,0,input_shape) || !OnnxSetOutputShape(handle,0,output_shape))
     {
      Print("Hybrid temporal shape failed: ",model_file," error=",GetLastError());
      OnnxRelease(handle); handle=INVALID_HANDLE; return false;
     }
   Print("Hybrid temporal ONNX ready: ",model_file," input=[1,",sequence_length,",32] output=[1,2]");
   return true;
  }

bool InitHybridPolicyOnnxSession(const bool enabled,const string model_file,long &handle)
  {
   handle=INVALID_HANDLE;
   if(!enabled) return true;
   uint flags=0;
   if(InpOnnxDebugLogs) flags|=ONNX_LOGLEVEL_INFO;
   ResetLastError();
   handle=OnnxCreate(model_file,flags);
   if(handle==INVALID_HANDLE)
     {
      Print("Hybrid policy ONNX create failed: ",model_file," error=",GetLastError());
      return false;
     }
   ulong input_shape[2]={1,HYBRID_POLICY_FEATURE_COUNT};
   ulong output_shape[2]={1,ONNX_CLASS_COUNT};
   if(!OnnxSetInputShape(handle,0,input_shape) || !OnnxSetOutputShape(handle,0,output_shape))
     {
      Print("Hybrid policy shape failed: ",model_file," error=",GetLastError());
      OnnxRelease(handle); handle=INVALID_HANDLE; return false;
     }
   Print("Hybrid policy ONNX ready: ",model_file," input=[1,36] output=[1,3]");
   return true;
  }

bool RunDirectionOnnx(const long handle,const int sequence_length,double &p_down,double &p_up)
  {
   p_down=0.5; p_up=0.5;
   if(handle==INVALID_HANDLE || sequence_length<=1 || !ValidSequenceLength(sequence_length)) return false;
   int available=MathMin(g_featureHistoryCount,sequence_length);
   if(available<=0) return false;
   matrixf input_tensor(sequence_length,ONNX_FEATURE_COUNT);
   int missing=sequence_length-available;
   int first=g_featureHistoryCount-available;
   for(int r=0;r<sequence_length;r++)
     {
      int src=(r<missing ? first : first+(r-missing));
      for(int j=0;j<ONNX_FEATURE_COUNT;j++) input_tensor[r][j]=(float)g_featureHistory[src*ONNX_FEATURE_COUNT+j];
     }
   vectorf output(DIRECTION_CLASS_COUNT);
   ulong run_flags=ONNX_NO_CONVERSION;
   if(InpOnnxDebugLogs) run_flags|=ONNX_LOGLEVEL_INFO;
   ResetLastError();
   if(!OnnxRun(handle,run_flags,input_tensor,output))
     {
      Print("Hybrid temporal OnnxRun failed error=",GetLastError());
      return false;
     }
   double a=(double)output[0],b=(double)output[1];
   if(a>=0.0 && b>=0.0 && MathAbs((a+b)-1.0)<0.02)
     {
      p_down=a; p_up=b;
     }
   else
     {
      double m=MathMax(a,b);
      double ea=MathExp(Clamp(a-m,-60.0,60.0));
      double eb=MathExp(Clamp(b-m,-60.0,60.0));
      double z=ea+eb;
      if(z<=0.0) return false;
      p_down=ea/z; p_up=eb/z;
     }
   return true;
  }

OnnxPrediction RunHybridOnnx(const long temporal_handle,const long policy_handle,const double &features[],const int sequence_length)
  {
   OnnxPrediction p=EmptyPrediction();
   if(policy_handle==INVALID_HANDLE || ArraySize(features)!=ONNX_FEATURE_COUNT) return p;
   double p_down=0.5,p_up=0.5;
   if(!RunDirectionOnnx(temporal_handle,sequence_length,p_down,p_up)) return p;
   double temporal_direction=p_up-p_down;
   double temporal_confidence=MathMax(p_down,p_up);
   matrixf input_tensor(1,HYBRID_POLICY_FEATURE_COUNT);
   for(int j=0;j<ONNX_FEATURE_COUNT;j++) input_tensor[0][j]=(float)features[j];
   input_tensor[0][32]=(float)p_down;
   input_tensor[0][33]=(float)p_up;
   input_tensor[0][34]=(float)temporal_direction;
   input_tensor[0][35]=(float)temporal_confidence;
   vectorf output(ONNX_CLASS_COUNT);
   ulong run_flags=ONNX_NO_CONVERSION;
   if(InpOnnxDebugLogs) run_flags|=ONNX_LOGLEVEL_INFO;
   ResetLastError();
   if(!OnnxRun(policy_handle,run_flags,input_tensor,output))
     {
      Print("Hybrid policy OnnxRun failed error=",GetLastError());
      return p;
     }
   double a=(double)output[0],b=(double)output[1],c=(double)output[2];
   bool probs=(a>=0.0 && b>=0.0 && c>=0.0 && MathAbs((a+b+c)-1.0)<0.02);
   if(probs) { p.sell_prob=a; p.skip_prob=b; p.buy_prob=c; }
   else Softmax3(a,b,c,p.sell_prob,p.skip_prob,p.buy_prob);
   p.directional=p.buy_prob-p.sell_prob;
   p.take_prob=1.0-p.skip_prob;
   p.valid=true;
   return p;
  }

void Softmax3(const double a,const double b,const double c,double &pa,double &pb,double &pc)
  {
   double m=MathMax(a,MathMax(b,c));
   double ea=MathExp(Clamp(a-m,-60.0,60.0));
   double eb=MathExp(Clamp(b-m,-60.0,60.0));
   double ec=MathExp(Clamp(c-m,-60.0,60.0));
   double sum=ea+eb+ec;
   if(sum<=0.0) { pa=pb=pc=1.0/3.0; return; }
   pa=ea/sum; pb=eb/sum; pc=ec/sum;
  }

void BuildFeatures(const MarketSnapshot &s,const FamilySignal &f[],const double rule_score,double &x[])
  {
   ArrayResize(x,ONNX_FEATURE_COUNT);
   double atr_pct=SafeDiv(s.atr,s.close1,0.0);
   x[0] =Clamp(SafeDiv(s.ret1,atr_pct,0.0),-5.0,5.0);
   x[1] =Clamp(SafeDiv(s.ret3,atr_pct,0.0),-8.0,8.0);
   x[2] =Clamp(SafeDiv(s.ret6,atr_pct,0.0),-12.0,12.0);
   x[3] =Clamp((s.ma_fast-s.close1)/s.atr,-5.0,5.0);
   x[4] =Clamp((s.ma_slow-s.close1)/s.atr,-8.0,8.0);
   x[5] =Clamp((s.ma_fast-s.ma_fast_prev)/s.atr,-2.0,2.0);
   x[6] =Clamp(s.adx/100.0,0.0,1.0);
   x[7] =Clamp(s.plus_di/100.0,0.0,1.0);
   x[8] =Clamp(s.minus_di/100.0,0.0,1.0);
   x[9] =Clamp((s.rsi-50.0)/50.0,-1.0,1.0);
   x[10]=Clamp(atr_pct*100.0,0.0,10.0);
   x[11]=Clamp(s.atr_ratio,0.0,4.0);
   x[12]=Clamp(s.bb_z,-3.0,3.0);
   x[13]=Clamp(s.bb_width_pct,0.0,20.0);
   x[14]=Clamp01(s.efficiency10);
   x[15]=Clamp(s.body_atr,0.0,5.0);
   x[16]=Clamp(s.upper_wick_atr,0.0,5.0);
   x[17]=Clamp(s.lower_wick_atr,0.0,5.0);
   x[18]=Clamp(s.range_atr,0.0,8.0);
   x[19]=Clamp(s.volume_z,-5.0,5.0);
   MqlDateTime dt;
   TimeToStruct(s.bar_time,dt);
   double pi=3.14159265358979323846;
   x[20]=MathSin(2.0*pi*(double)dt.hour/24.0);
   x[21]=MathCos(2.0*pi*(double)dt.hour/24.0);
   x[22]=MathSin(2.0*pi*(double)dt.day_of_week/7.0);
   x[23]=MathCos(2.0*pi*(double)dt.day_of_week/7.0);
   x[24]=f[FAMILY_TREND].signal*f[FAMILY_TREND].quality;
   x[25]=f[FAMILY_RANGE].signal*f[FAMILY_RANGE].quality;
   x[26]=f[FAMILY_BREAKOUT].signal*f[FAMILY_BREAKOUT].quality;
   x[27]=f[FAMILY_PULLBACK].signal*f[FAMILY_PULLBACK].quality;
   x[28]=f[FAMILY_SESSION].signal*f[FAMILY_SESSION].quality;
   x[29]=f[FAMILY_SHOCK].signal*f[FAMILY_SHOCK].quality;
   x[30]=f[FAMILY_RELATIVE].signal*f[FAMILY_RELATIVE].quality;
   x[31]=Clamp(rule_score,-1.0,1.0);
  }

OnnxPrediction EmptyPrediction()
  {
   OnnxPrediction p;
   p.valid=false; p.sell_prob=0.0; p.skip_prob=1.0; p.buy_prob=0.0;
   p.directional=0.0; p.take_prob=0.0;
   return p;
  }

OnnxPrediction RunOnnx(const long handle,const double &features[],const int sequence_length)
  {
   OnnxPrediction p=EmptyPrediction();
   if(handle==INVALID_HANDLE || !ValidSequenceLength(sequence_length)) return p;
   matrixf input_tensor(sequence_length,ONNX_FEATURE_COUNT);
   if(sequence_length<=1)
     {
      for(int i=0;i<ONNX_FEATURE_COUNT;i++) input_tensor[0][i]=(float)features[i];
     }
   else
     {
      int available=MathMin(g_featureHistoryCount,sequence_length);
      if(available<=0) return p;
      int missing=sequence_length-available;
      int first=g_featureHistoryCount-available;
      for(int r=0;r<sequence_length;r++)
        {
         int src=(r<missing ? first : first+(r-missing));
         for(int j=0;j<ONNX_FEATURE_COUNT;j++)
           input_tensor[r][j]=(float)g_featureHistory[src*ONNX_FEATURE_COUNT+j];
        }
     }
   vectorf output(ONNX_CLASS_COUNT);
   ulong run_flags=ONNX_NO_CONVERSION;
   if(InpOnnxDebugLogs) run_flags|=ONNX_LOGLEVEL_INFO;
   ResetLastError();
   if(!OnnxRun(handle,run_flags,input_tensor,output))
     {
      Print("OnnxRun failed seq=",sequence_length,", error=",GetLastError());
      return p;
     }
   double a=(double)output[0],b=(double)output[1],c=(double)output[2];
   bool probs=(a>=0.0 && b>=0.0 && c>=0.0 && MathAbs((a+b+c)-1.0)<0.02);
   if(probs) { p.sell_prob=a; p.skip_prob=b; p.buy_prob=c; }
   else Softmax3(a,b,c,p.sell_prob,p.skip_prob,p.buy_prob);
   p.directional=p.buy_prob-p.sell_prob; p.take_prob=1.0-p.skip_prob; p.valid=true;
   return p;
  }

DecisionPolicy EmptyPolicy()
  {
   DecisionPolicy p;
   p.valid=false; p.take_threshold=0.52; p.buy_threshold=0.0; p.sell_threshold=0.0;
   p.directional_margin=0.0; p.max_entropy=1.10; p.regime_mode="ALL";
   return p;
  }

bool LoadDecisionPolicy(const bool enabled,const string file_name,DecisionPolicy &p)
  {
   p=EmptyPolicy();
   if(!enabled) return true;
   int h=FileOpen(file_name,FILE_READ|FILE_CSV|FILE_ANSI|FILE_COMMON|FILE_SHARE_READ,';');
   if(h==INVALID_HANDLE)
     {
      Print("Policy FileOpen failed: ",file_name," error=",GetLastError());
      return false;
     }
   if(!FileIsEnding(h)) { FileReadString(h); if(!FileIsEnding(h)) FileReadString(h); }
   string schema="";
   while(!FileIsEnding(h))
     {
      string key=FileReadString(h);
      if(FileIsEnding(h) && StringLen(key)==0) break;
      string val=FileReadString(h);
      if(key=="schema") schema=val;
      else if(key=="take_threshold") p.take_threshold=StringToDouble(val);
      else if(key=="buy_threshold") p.buy_threshold=StringToDouble(val);
      else if(key=="sell_threshold") p.sell_threshold=StringToDouble(val);
      else if(key=="directional_margin") p.directional_margin=StringToDouble(val);
      else if(key=="max_entropy") p.max_entropy=StringToDouble(val);
      else if(key=="regime_mode") p.regime_mode=val;
     }
   FileClose(h);
   if(schema!="CP_POLICY_V1")
     {
      Print("Unsupported decision policy schema: ",schema," file=",file_name);
      return false;
     }
   p.take_threshold=Clamp(p.take_threshold,0.0,0.99);
   p.buy_threshold=Clamp(p.buy_threshold,0.0,0.99);
   p.sell_threshold=Clamp(p.sell_threshold,0.0,0.99);
   p.directional_margin=Clamp(p.directional_margin,0.0,1.0);
   p.max_entropy=Clamp(p.max_entropy,0.0,1.20);
   p.valid=true;
   Print("CP_POLICY_V1 ready: ",file_name," take=",DoubleToString(p.take_threshold,2)," buy=",DoubleToString(p.buy_threshold,2)," sell=",DoubleToString(p.sell_threshold,2)," margin=",DoubleToString(p.directional_margin,2)," entropy=",DoubleToString(p.max_entropy,2)," regime=",p.regime_mode);
   return true;
  }

double ProbabilityEntropy(const OnnxPrediction &p)
  {
   if(!p.valid) return 99.0;
   double e=0.0;
   double v[3]={p.sell_prob,p.skip_prob,p.buy_prob};
   for(int i=0;i<3;i++) if(v[i]>1e-12) e-=v[i]*MathLog(v[i]);
   return e;
  }

string PolicyRegime(const MarketSnapshot &s)
  {
   if(s.range_atr>=3.0) return "SHOCK";
   double adx_scaled=s.adx/100.0;
   if(adx_scaled>=0.25) return "TREND";
   if(adx_scaled<=0.18) return "RANGE";
   return "TRANSITION";
  }

bool PolicyRegimeAllowed(const string mode,const string regime)
  {
   if(mode=="ALL") return true;
   if(mode=="NON_SHOCK") return regime!="SHOCK";
   if(mode=="TREND") return regime=="TREND";
   if(mode=="RANGE") return regime=="RANGE";
   if(mode=="TREND_RANGE") return (regime=="TREND" || regime=="RANGE");
   if(mode=="TRANSITION") return regime=="TRANSITION";
   return false;
  }

bool EvaluatePolicyDecision(const OnnxPrediction &pred,const DecisionPolicy &policy,const MarketSnapshot &s,const double rule_score,const double consensus,int &direction,string &reason)
  {
   direction=0; reason="";
   if(!pred.valid) { reason="POLICY_ONNX_INVALID"; return false; }
   if(!policy.valid) { reason="POLICY_MISSING"; return false; }
   double final_score=(1.0-Clamp01(InpOnnxBlend))*rule_score+Clamp01(InpOnnxBlend)*pred.directional;
   if(pred.take_prob<policy.take_threshold) { reason="POLICY_TAKE"; return false; }
   if(MathAbs(pred.buy_prob-pred.sell_prob)<policy.directional_margin) { reason="POLICY_MARGIN"; return false; }
   if(ProbabilityEntropy(pred)>policy.max_entropy) { reason="POLICY_ENTROPY"; return false; }
   if(!PolicyRegimeAllowed(policy.regime_mode,PolicyRegime(s))) { reason="POLICY_REGIME"; return false; }
   if(consensus<InpMinConsensus) { reason="POLICY_CONSENSUS"; return false; }
   if(CurrentSpreadPoints()>InpMaxSpreadPoints) { reason="POLICY_SPREAD"; return false; }
   if(s.range_atr>=InpShockHaltATR) { reason="POLICY_SHOCK"; return false; }
   if(MathAbs(final_score)<InpEntryThreshold) { reason="POLICY_ENTRY"; return false; }
   if(final_score>0.0)
     {
      if(pred.buy_prob<policy.buy_threshold) { reason="POLICY_BUY_PROB"; return false; }
      direction=1; return true;
     }
   if(final_score<0.0)
     {
      if(pred.sell_prob<policy.sell_threshold) { reason="POLICY_SELL_PROB"; return false; }
      direction=-1; return true;
     }
   reason="POLICY_DIRECTION";
   return false;
  }

bool CalculatePlannedStopRisk(const ENUM_ORDER_TYPE action,const double volume,const double entry_price,const double stop_price,double &risk_money)
  {
   risk_money=0.0;
   if(volume<=0.0 || entry_price<=0.0 || stop_price<=0.0) return false;
   if(action==ORDER_TYPE_BUY && stop_price>=entry_price) return false;
   if(action==ORDER_TYPE_SELL && stop_price<=entry_price) return false;

   double stop_pnl=0.0;
   ResetLastError();
   if(!OrderCalcProfit(action,_Symbol,volume,entry_price,stop_price,stop_pnl)) return false;
   risk_money=MathAbs(stop_pnl);
   return MathIsValidNumber(risk_money) && risk_money>0.0;
  }

double CalculateVolume(const int direction,const double entry_price,const double stop_price,string &reason,double &planned_risk)
  {
   reason="";
   planned_risk=0.0;
   if(direction!=1 && direction!=-1) { reason="RISK_DIRECTION_INVALID"; return 0.0; }
   if(entry_price<=0.0 || stop_price<=0.0) { reason="RISK_PRICE_INVALID"; return 0.0; }

   double equity=AccountInfoDouble(ACCOUNT_EQUITY);
   double risk_money=equity*MathMax(0.0,InpRiskPct)/100.0;
   if(!MathIsValidNumber(equity) || equity<=0.0 || !MathIsValidNumber(risk_money) || risk_money<=0.0)
     { reason="RISK_BUDGET_INVALID"; return 0.0; }

   double vmin=SymbolInfoDouble(_Symbol,SYMBOL_VOLUME_MIN);
   double vmax=SymbolInfoDouble(_Symbol,SYMBOL_VOLUME_MAX);
   double step=SymbolInfoDouble(_Symbol,SYMBOL_VOLUME_STEP);
   if(!MathIsValidNumber(vmin) || !MathIsValidNumber(vmax) || !MathIsValidNumber(step) ||
      vmin<=0.0 || vmax<vmin || step<=0.0)
     { reason="RISK_VOLUME_SPEC_INVALID"; return 0.0; }

   ENUM_ORDER_TYPE action;
   if(direction>0) action=ORDER_TYPE_BUY;
   else action=ORDER_TYPE_SELL;
   double min_risk=0.0;
   if(!CalculatePlannedStopRisk(action,vmin,entry_price,stop_price,min_risk))
     { reason="RISK_CALC_FAILED"; return 0.0; }

   // Hard fail-closed invariant: broker minimum lot must never force risk above InpRiskPct.
   if(min_risk>risk_money)
     {
      reason="RISK_MIN_VOLUME_EXCEEDS_CAP";
      return 0.0;
     }

   long max_index=(long)MathFloor((vmax-vmin)/step+1.0e-9);
   if(max_index<0) max_index=0;
   long low=0,high=max_index,best=-1;
   int digits=VolumeDigits();

   // Binary-search the largest broker-valid volume whose exact stop loss,
   // calculated in account currency by OrderCalcProfit(), does not exceed the risk cap.
   while(low<=high)
     {
      long mid=low+(high-low)/2;
      double vol=NormalizeDouble(vmin+(double)mid*step,digits);
      double candidate_risk=0.0;
      if(!CalculatePlannedStopRisk(action,vol,entry_price,stop_price,candidate_risk))
        { reason="RISK_CALC_FAILED"; return 0.0; }

      if(candidate_risk<=risk_money)
        {
         best=mid;
         low=mid+1;
        }
      else high=mid-1;
     }

   if(best<0) { reason="RISK_NO_VALID_VOLUME"; return 0.0; }
   double volume=NormalizeDouble(vmin+(double)best*step,digits);
   double final_risk=0.0;
   if(!CalculatePlannedStopRisk(action,volume,entry_price,stop_price,final_risk))
     { reason="RISK_CALC_FAILED"; return 0.0; }

   // Final exact verification after volume normalization. Never submit an order
   // whose planned initial stop risk is above the Owner-configured RiskPct cap.
   if(final_risk>risk_money)
     { reason="RISK_CAP_EXCEEDED"; return 0.0; }

   planned_risk=final_risk;
   return volume;
  }

bool TradingGate(const MarketSnapshot &s,const double consensus,string &reason)
  {
   reason="";
   if(DailyLossPct()>=InpMaxDailyLossPct) { reason="DAILY_LOSS_LIMIT"; return false; }
   if(CurrentSpreadPoints()>InpMaxSpreadPoints) { reason="SPREAD"; return false; }
   if(s.range_atr>=InpShockHaltATR) { reason="EXTREME_SHOCK"; return false; }
   if(consensus<InpMinConsensus) { reason="LOW_CONSENSUS"; return false; }
   return true;
  }

bool OpenTrade(const int direction,const MarketSnapshot &s,const double final_score)
  {
   if(!InpAllowLiveTrading) return false;
   MqlTick tick;
   if(!SymbolInfoTick(_Symbol,tick)) return false;
   double stop_distance=s.atr*InpSL_ATR;
   double take_distance=s.atr*InpTP_ATR;
   double entry_price=0.0;
   double requested_sl=0.0;
   double tp=0.0;
   if(direction>0)
     {
      entry_price=tick.ask;
      requested_sl=NormalizeDouble(entry_price-stop_distance,_Digits);
      tp=NormalizeDouble(entry_price+take_distance,_Digits);
     }
   else if(direction<0)
     {
      entry_price=tick.bid;
      requested_sl=NormalizeDouble(entry_price+stop_distance,_Digits);
      tp=NormalizeDouble(entry_price-take_distance,_Digits);
     }
   else return false;

   string risk_reason="";
   double planned_risk=0.0;
   double vol=CalculateVolume(direction,entry_price,requested_sl,risk_reason,planned_risk);
   if(vol<=0.0 || !MathIsValidNumber(planned_risk) || planned_risk<=0.0)
     {
      Print("Trade skipped by risk cap. reason=",risk_reason,
            " risk_pct=",DoubleToString(InpRiskPct,6),
            " equity=",DoubleToString(AccountInfoDouble(ACCOUNT_EQUITY),2));
      return false;
     }

   g_trade.SetExpertMagicNumber(InpMagic);
   g_trade.SetDeviationInPoints(InpSlippagePoints);
   bool ok=false;
   if(direction>0)
      ok=g_trade.Buy(vol,_Symbol,0.0,requested_sl,tp,StringFormat("CP BUY %.3f",final_score));
   else
      ok=g_trade.Sell(vol,_Symbol,0.0,requested_sl,tp,StringFormat("CP SELL %.3f",final_score));
   if(!ok) Print("Trade open failed. retcode=",g_trade.ResultRetcode()," ",g_trade.ResultRetcodeDescription());
   return ok;
  }

void ManagePosition(const double final_score)
  {
   ulong ticket; long type; datetime open_time;
   if(!GetOwnPosition(ticket,type,open_time)) return;
   bool close=false; string why="";
   if(InpMaxHoldBars>0 && BarsHeld(open_time)>=InpMaxHoldBars) { close=true; why="TIME_EXIT"; }
   if(!close)
     {
      if(type==POSITION_TYPE_BUY && final_score<=-InpExitReverseThreshold) { close=true; why="REVERSE_EXIT"; }
      else if(type==POSITION_TYPE_SELL && final_score>=InpExitReverseThreshold) { close=true; why="REVERSE_EXIT"; }
     }
   if(close && InpAllowLiveTrading)
     {
      g_trade.SetExpertMagicNumber(InpMagic);
      if(!g_trade.PositionClose(ticket)) Print("Position close failed. ",why," retcode=",g_trade.ResultRetcode());
      else Print("Position closed: ",why);
     }
  }


int AcquireTrainingWriterLock()
  {
   string lock_name=InpTrainingFile+".lock";
   for(int i=0;i<200;i++)
     {
      ResetLastError();
      int h=FileOpen(lock_name,FILE_READ|FILE_WRITE|FILE_BIN|FILE_COMMON);
      if(h!=INVALID_HANDLE) return h;
      Sleep(10);
     }
   Print("Training writer lock timeout: ",lock_name," error=",GetLastError());
   return INVALID_HANDLE;
  }

void ReleaseTrainingWriterLock(const int h)
  {
   if(h!=INVALID_HANDLE) FileClose(h);
  }

int TrainingFindPosition(const datetime t,bool &found)
  {
   int n=ArraySize(g_trainingTimes);
   int lo=0,hi=n-1;
   while(lo<=hi)
     {
      int mid=lo+(hi-lo)/2;
      if(g_trainingTimes[mid]==t) { found=true; return mid; }
      if(g_trainingTimes[mid]<t) lo=mid+1;
      else hi=mid-1;
     }
   found=false;
   return lo;
  }

bool TrainingTimeExists(const datetime t)
  {
   bool found=false;
   TrainingFindPosition(t,found);
   return found;
  }

void TrainingTimeInsert(const datetime t)
  {
   bool found=false;
   int pos=TrainingFindPosition(t,found);
   if(found) return;
   int n=ArraySize(g_trainingTimes);
   if(ArrayResize(g_trainingTimes,n+1)!=n+1)
     {
      Print("Training index ArrayResize failed");
      return;
     }
   for(int i=n;i>pos;i--) g_trainingTimes[i]=g_trainingTimes[i-1];
   g_trainingTimes[pos]=t;
  }

void WriteTrainingHeader(const int h)
  {
   FileWrite(h,
      "contract","signal_time","decision_bar_time","symbol","period",
      "open","high","low","close","atr","decision_bid","decision_ask","spread_points",
      "sl_atr","tp_atr","max_hold_bars","consensus",
      "ret1_atr","ret3_atr","ret6_atr","fast_ma_gap_atr","slow_ma_gap_atr","fast_ma_slope_atr",
      "adx_scaled","plus_di_scaled","minus_di_scaled","rsi_centered","atr_percent","atr_ratio",
      "bollinger_z","bollinger_width_pct","efficiency10","candle_body_atr","upper_wick_atr",
      "lower_wick_atr","range_atr","tick_volume_z","hour_sin","hour_cos","weekday_sin","weekday_cos",
      "trend_family","range_family","breakout_family","pullback_family","session_family","shock_family",
      "relative_family","rule_meta_score");
   FileFlush(h);
  }

bool RefreshTrainingIndex(const int h,const ulong from_offset,const bool reset_index)
  {
   if(reset_index) ArrayResize(g_trainingTimes,0);
   if(!FileSeek(h,(long)from_offset,SEEK_SET))
     {
      Print("Training FileSeek failed. offset=",from_offset," error=",GetLastError());
      return false;
     }
   while(!FileIsEnding(h))
     {
      string contract=FileReadString(h);
      if(FileIsEnding(h) && StringLen(contract)==0) break;
      string signal_time=FileReadString(h);
      string decision_bar_time=FileReadString(h);
      string symbol=FileReadString(h);
      string period_text=FileReadString(h);
      for(int c=5;c<TRAINING_COLUMN_COUNT;c++) FileReadString(h);

      if(contract=="contract") continue;
      if(contract!=MAX_MTF_FEATURE_CONTRACT) continue;
      if(symbol!=_Symbol) continue;
      if((int)StringToInteger(period_text)!=(int)g_roleTf[MTF_ROLE_MAIN]) continue;
      datetime t=StringToTime(signal_time);
      if(t>0) TrainingTimeInsert(t);
     }
   g_trainingKnownSize=FileSize(h);
   g_trainingIndexReady=true;
   return true;
  }

int OpenTrainingCsvExclusiveWriter()
  {
   // FILE_SHARE_WRITE is deliberately absent. Every v1.06 writer serializes
   // through the .lock file, so another non-cooperating writer must fail closed.
   return FileOpen(InpTrainingFile,FILE_READ|FILE_WRITE|FILE_CSV|FILE_ANSI|FILE_COMMON|FILE_SHARE_READ,';');
  }

void OpenTrainingData()
  {
   if(!InpWriteTrainingData) return;
   int lock_h=AcquireTrainingWriterLock();
   if(lock_h==INVALID_HANDLE) return;
   int h=OpenTrainingCsvExclusiveWriter();
   if(h==INVALID_HANDLE)
     {
      Print("Training FileOpen failed. Another writer may still be active. error=",GetLastError());
      ReleaseTrainingWriterLock(lock_h);
      return;
     }
   if(FileSize(h)==0) WriteTrainingHeader(h);
   if(!RefreshTrainingIndex(h,0,true))
      Print("Training index preload failed; writer will retry on next bar.");
   else
      Print("Training dedup index ready. Existing unique keys for this symbol/timeframe=",ArraySize(g_trainingTimes));
   FileClose(h);
   ReleaseTrainingWriterLock(lock_h);
  }

void LogTrainingRow(const MarketSnapshot &s,const double &features[],const double consensus)
  {
   if(!InpWriteTrainingData) return;
   if(s.bar_time==g_lastTrainingSignalTime) return;
   if(ArraySize(features)!=ONNX_FEATURE_COUNT) return;

   int lock_h=AcquireTrainingWriterLock();
   if(lock_h==INVALID_HANDLE) return;
   int h=OpenTrainingCsvExclusiveWriter();
   if(h==INVALID_HANDLE)
     {
      Print("Training append blocked. Stop/upgrade any legacy v1.03 writer. error=",GetLastError());
      ReleaseTrainingWriterLock(lock_h);
      return;
     }
   if(FileSize(h)==0)
     {
      WriteTrainingHeader(h);
      g_trainingIndexReady=false;
      g_trainingKnownSize=0;
     }

   ulong current_size=FileSize(h);
   bool refresh_ok=true;
   if(!g_trainingIndexReady || current_size<g_trainingKnownSize)
      refresh_ok=RefreshTrainingIndex(h,0,true);
   else if(current_size>g_trainingKnownSize)
      refresh_ok=RefreshTrainingIndex(h,g_trainingKnownSize,false);
   if(!refresh_ok)
     {
      FileClose(h);
      ReleaseTrainingWriterLock(lock_h);
      return;
     }

   // First-write-wins: live rows already present are never overwritten by an
   // overlapping tester run. A later backtest appends only missing Main-TF gaps.
   if(TrainingTimeExists(s.bar_time))
     {
      g_lastTrainingSignalTime=s.bar_time;
      FileClose(h);
      ReleaseTrainingWriterLock(lock_h);
      return;
     }

   MqlTick tick;
   if(!SymbolInfoTick(_Symbol,tick))
     {
      FileClose(h);
      ReleaseTrainingWriterLock(lock_h);
      return;
     }
   double point=SymbolInfoDouble(_Symbol,SYMBOL_POINT);
   double spread_points=(point>0.0 ? (tick.ask-tick.bid)/point : 0.0);
   datetime decision_bar_time=iTime(_Symbol,g_roleTf[MTF_ROLE_MAIN],0);

   FileSeek(h,0,SEEK_END);
   FileWrite(h,
      MAX_MTF_FEATURE_CONTRACT,
      TimeToString(s.bar_time,TIME_DATE|TIME_MINUTES),
      TimeToString(decision_bar_time,TIME_DATE|TIME_MINUTES),
      _Symbol,IntegerToString((int)g_roleTf[MTF_ROLE_MAIN]),
      DoubleToString(s.open1,_Digits),DoubleToString(s.high1,_Digits),DoubleToString(s.low1,_Digits),DoubleToString(s.close1,_Digits),
      DoubleToString(s.atr,_Digits),DoubleToString(tick.bid,_Digits),DoubleToString(tick.ask,_Digits),DoubleToString(spread_points,2),
      DoubleToString(InpSL_ATR,4),DoubleToString(InpTP_ATR,4),IntegerToString(InpMaxHoldBars),DoubleToString(consensus,6),
      DoubleToString(features[0],8),DoubleToString(features[1],8),DoubleToString(features[2],8),DoubleToString(features[3],8),
      DoubleToString(features[4],8),DoubleToString(features[5],8),DoubleToString(features[6],8),DoubleToString(features[7],8),
      DoubleToString(features[8],8),DoubleToString(features[9],8),DoubleToString(features[10],8),DoubleToString(features[11],8),
      DoubleToString(features[12],8),DoubleToString(features[13],8),DoubleToString(features[14],8),DoubleToString(features[15],8),
      DoubleToString(features[16],8),DoubleToString(features[17],8),DoubleToString(features[18],8),DoubleToString(features[19],8),
      DoubleToString(features[20],8),DoubleToString(features[21],8),DoubleToString(features[22],8),DoubleToString(features[23],8),
      DoubleToString(features[24],8),DoubleToString(features[25],8),DoubleToString(features[26],8),DoubleToString(features[27],8),
      DoubleToString(features[28],8),DoubleToString(features[29],8),DoubleToString(features[30],8),DoubleToString(features[31],8));
   FileFlush(h);
   TrainingTimeInsert(s.bar_time);
   g_trainingKnownSize=FileSize(h);
   g_lastTrainingSignalTime=s.bar_time;
   FileClose(h);
   ReleaseTrainingWriterLock(lock_h);
  }

bool ChampionPositionTracked(const ulong position_id)
  {
   if(position_id==0) return false;
   for(int i=0;i<ArraySize(g_championPositionIds);i++)
      if(g_championPositionIds[i]==position_id) return true;
   return false;
  }

void TrackChampionPosition(const ulong position_id)
  {
   if(position_id==0 || ChampionPositionTracked(position_id)) return;
   int n=ArraySize(g_championPositionIds);
   if(ArrayResize(g_championPositionIds,n+1)==n+1) g_championPositionIds[n]=position_id;
  }

void RebuildChampionPositionTracking()
  {
   ArrayResize(g_championPositionIds,0);
   for(int i=PositionsTotal()-1;i>=0;i--)
     {
      ulong ticket=PositionGetTicket(i);
      if(ticket==0) continue;
      if(PositionGetString(POSITION_SYMBOL)!=_Symbol) continue;
      if((long)PositionGetInteger(POSITION_MAGIC)!=InpMagic) continue;
      TrackChampionPosition((ulong)PositionGetInteger(POSITION_IDENTIFIER));
     }
  }

void OpenTradeAuditCsv()
  {
   // Optimization agents are evidence workers, not live/shadow audit writers.
   // Disabling these files there prevents multi-agent FILE_COMMON contention.
   if(MQLInfoInteger(MQL_OPTIMIZATION)) return;
   if(InpWriteChampionTrades)
     {
      g_championTradesCsv=FileOpen(InpChampionTradesFile,FILE_READ|FILE_WRITE|FILE_CSV|FILE_ANSI|FILE_COMMON|FILE_SHARE_READ|FILE_SHARE_WRITE,';');
      if(g_championTradesCsv==INVALID_HANDLE) Print("Champion trade audit FileOpen failed: ",GetLastError());
      else
        {
         if(FileSize(g_championTradesCsv)==0)
            FileWrite(g_championTradesCsv,
               "time","symbol","period","event","position_id","deal_ticket","order_ticket","deal_side","volume","price","sl","tp",
               "profit","commission","swap","fee","net","initial_risk_money","deal_reason","magic",
               "champion_model_family","champion_topology","champion_hybrid_policy_family","comment");
         FileSeek(g_championTradesCsv,0,SEEK_END); FileFlush(g_championTradesCsv);
        }
     }
   if(InpWriteShadowTrades)
     {
      g_shadowTradesCsv=FileOpen(InpShadowTradesFile,FILE_READ|FILE_WRITE|FILE_CSV|FILE_ANSI|FILE_COMMON|FILE_SHARE_READ|FILE_SHARE_WRITE,';');
      if(g_shadowTradesCsv==INVALID_HANDLE) Print("Shadow trade audit FileOpen failed: ",GetLastError());
      else
        {
         if(FileSize(g_shadowTradesCsv)==0)
            FileWrite(g_shadowTradesCsv,
               "decision_time","signal_bar_time","symbol","period","event","direction","entry_price","sl","tp","planned_volume","planned_risk_money",
               "sell_prob","skip_prob","buy_prob","take_prob","rule_score","consensus","policy_reason",
               "shadow_model_family","shadow_topology","shadow_hybrid_policy_family","executed");
         FileSeek(g_shadowTradesCsv,0,SEEK_END); FileFlush(g_shadowTradesCsv);
        }
     }
  }

void LogChampionDealAudit(const ulong deal)
  {
   if(g_championTradesCsv==INVALID_HANDLE || deal==0) return;
   if(!HistoryDealSelect(deal)) return;
   string symbol=HistoryDealGetString(deal,DEAL_SYMBOL);
   if(symbol!=_Symbol) return;
   long entry=(long)HistoryDealGetInteger(deal,DEAL_ENTRY);
   long magic=(long)HistoryDealGetInteger(deal,DEAL_MAGIC);
   ulong position_id=(ulong)HistoryDealGetInteger(deal,DEAL_POSITION_ID);
   bool own_entry=(entry==DEAL_ENTRY_IN && magic==InpMagic);
   bool ours=own_entry || ChampionPositionTracked(position_id) || magic==InpMagic;
   if(!ours) return;
   if(own_entry) TrackChampionPosition(position_id);

   long deal_type=(long)HistoryDealGetInteger(deal,DEAL_TYPE);
   string side=(deal_type==DEAL_TYPE_BUY?"BUY":(deal_type==DEAL_TYPE_SELL?"SELL":"OTHER"));
   string event=(entry==DEAL_ENTRY_IN?"ENTRY":((entry==DEAL_ENTRY_OUT || entry==DEAL_ENTRY_OUT_BY)?"EXIT":(entry==DEAL_ENTRY_INOUT?"INOUT":"OTHER")));
   double profit=HistoryDealGetDouble(deal,DEAL_PROFIT);
   double commission=HistoryDealGetDouble(deal,DEAL_COMMISSION);
   double swap=HistoryDealGetDouble(deal,DEAL_SWAP);
   double fee=HistoryDealGetDouble(deal,DEAL_FEE);
   double net=profit+commission+swap+fee;
   double initial_risk=0.0;
   if(entry==DEAL_ENTRY_IN) StrategyOptimizerInitialRiskFromDeal(deal,initial_risk);
   ulong order_ticket=(ulong)HistoryDealGetInteger(deal,DEAL_ORDER);
   double sl=HistoryDealGetDouble(deal,DEAL_SL);
   double tp=HistoryDealGetDouble(deal,DEAL_TP);
   if(order_ticket>0)
     {
      if(sl<=0.0) sl=HistoryOrderGetDouble(order_ticket,ORDER_SL);
      if(tp<=0.0) tp=HistoryOrderGetDouble(order_ticket,ORDER_TP);
     }
   FileWrite(g_championTradesCsv,
      TimeToString((datetime)HistoryDealGetInteger(deal,DEAL_TIME),TIME_DATE|TIME_SECONDS),symbol,IntegerToString((int)g_roleTf[MTF_ROLE_MAIN]),event,
      StringFormat("%I64u",position_id),StringFormat("%I64u",deal),StringFormat("%I64u",order_ticket),side,
      DoubleToString(HistoryDealGetDouble(deal,DEAL_VOLUME),VolumeDigits()),DoubleToString(HistoryDealGetDouble(deal,DEAL_PRICE),_Digits),
      DoubleToString(sl,_Digits),DoubleToString(tp,_Digits),DoubleToString(profit,2),DoubleToString(commission,2),DoubleToString(swap,2),DoubleToString(fee,2),DoubleToString(net,2),
      DoubleToString(initial_risk,2),IntegerToString((int)HistoryDealGetInteger(deal,DEAL_REASON)),IntegerToString((int)magic),
      EnumToString(InpChampionModelFamily),(InpChampionHybrid?"TEMPORAL_TO_TREE":"STANDALONE"),EnumToString(InpChampionHybridPolicyFamily),HistoryDealGetString(deal,DEAL_COMMENT));
   FileFlush(g_championTradesCsv);
  }

void LogShadowTradeAudit(const MarketSnapshot &s,const OnnxPrediction &chall,const int direction,const string policy_reason,const double rule_score,const double consensus)
  {
   if(g_shadowTradesCsv==INVALID_HANDLE || !InpUseOnnxChallenger || !chall.valid || (direction!=1 && direction!=-1)) return;
   MqlTick tick;
   if(!SymbolInfoTick(_Symbol,tick)) return;
   double entry=(direction>0?tick.ask:tick.bid);
   double sl=(direction>0?entry-s.atr*InpSL_ATR:entry+s.atr*InpSL_ATR);
   double tp=(direction>0?entry+s.atr*InpTP_ATR:entry-s.atr*InpTP_ATR);
   sl=NormalizeDouble(sl,_Digits); tp=NormalizeDouble(tp,_Digits);
   string risk_reason=""; double planned_risk=0.0;
   double volume=CalculateVolume(direction,entry,sl,risk_reason,planned_risk);
   string event=(volume>0.0?"SHADOW_ENTRY_CANDIDATE":"SHADOW_RISK_REJECT");
   string reason=(volume>0.0?policy_reason:(policy_reason+"|"+risk_reason));
   FileWrite(g_shadowTradesCsv,
      TimeToString(TimeCurrent(),TIME_DATE|TIME_SECONDS),TimeToString(s.bar_time,TIME_DATE|TIME_MINUTES),_Symbol,IntegerToString((int)g_roleTf[MTF_ROLE_MAIN]),event,(direction>0?"BUY":"SELL"),
      DoubleToString(entry,_Digits),DoubleToString(sl,_Digits),DoubleToString(tp,_Digits),DoubleToString(volume,VolumeDigits()),DoubleToString(planned_risk,2),
      DoubleToString(chall.sell_prob,5),DoubleToString(chall.skip_prob,5),DoubleToString(chall.buy_prob,5),DoubleToString(chall.take_prob,5),
      DoubleToString(rule_score,5),DoubleToString(consensus,5),reason,
      EnumToString(InpChallengerModelFamily),(InpChallengerHybrid?"TEMPORAL_TO_TREE":"STANDALONE"),EnumToString(InpChallengerHybridPolicyFamily),"0");
   FileFlush(g_shadowTradesCsv);
  }

void OpenTelemetry()
  {
   if(!InpWriteTelemetry) return;
   g_csv=FileOpen(InpTelemetryFile,FILE_READ|FILE_WRITE|FILE_CSV|FILE_ANSI|FILE_COMMON|FILE_SHARE_READ|FILE_SHARE_WRITE,';');
   if(g_csv==INVALID_HANDLE) { Print("Telemetry FileOpen failed: ",GetLastError()); return; }
   if(FileSize(g_csv)==0)
     {
      FileWrite(g_csv,
         "time","symbol","period","close","atr","adx","rsi","atr_ratio","range_atr","bb_z",
         "trend_sig","trend_q","range_sig","range_q","breakout_sig","breakout_q",
         "pullback_sig","pullback_q","session_sig","session_q","shock_sig","shock_q",
         "relative_sig","relative_q","rule_score","consensus",
         "champ_valid","champ_sell","champ_skip","champ_buy","champ_take",
         "chall_valid","chall_sell","chall_skip","chall_buy","chall_take",
         "chall_policy_valid","chall_policy_decision","chall_policy_reason",
         "final_score","decision","gate_reason","live_enabled");
      FileFlush(g_csv);
     }
   FileSeek(g_csv,0,SEEK_END);
  }

void LogTelemetry(const MarketSnapshot &s,const FamilySignal &f[],const double rule_score,const double consensus,
                  const OnnxPrediction &champ,const OnnxPrediction &chall,const bool chall_policy_valid,const string chall_policy_decision,const string chall_policy_reason,const double final_score,
                  const string decision,const string gate_reason)
  {
   if(g_csv==INVALID_HANDLE) return;
   FileWrite(g_csv,
      TimeToString(s.bar_time,TIME_DATE|TIME_MINUTES),_Symbol,IntegerToString((int)g_roleTf[MTF_ROLE_MAIN]),
      DoubleToString(s.close1,_Digits),DoubleToString(s.atr,_Digits),DoubleToString(s.adx,3),DoubleToString(s.rsi,3),
      DoubleToString(s.atr_ratio,4),DoubleToString(s.range_atr,4),DoubleToString(s.bb_z,4),
      DoubleToString(f[FAMILY_TREND].signal,4),DoubleToString(f[FAMILY_TREND].quality,4),
      DoubleToString(f[FAMILY_RANGE].signal,4),DoubleToString(f[FAMILY_RANGE].quality,4),
      DoubleToString(f[FAMILY_BREAKOUT].signal,4),DoubleToString(f[FAMILY_BREAKOUT].quality,4),
      DoubleToString(f[FAMILY_PULLBACK].signal,4),DoubleToString(f[FAMILY_PULLBACK].quality,4),
      DoubleToString(f[FAMILY_SESSION].signal,4),DoubleToString(f[FAMILY_SESSION].quality,4),
      DoubleToString(f[FAMILY_SHOCK].signal,4),DoubleToString(f[FAMILY_SHOCK].quality,4),
      DoubleToString(f[FAMILY_RELATIVE].signal,4),DoubleToString(f[FAMILY_RELATIVE].quality,4),
      DoubleToString(rule_score,5),DoubleToString(consensus,5),
      champ.valid?"1":"0",DoubleToString(champ.sell_prob,5),DoubleToString(champ.skip_prob,5),DoubleToString(champ.buy_prob,5),DoubleToString(champ.take_prob,5),
      chall.valid?"1":"0",DoubleToString(chall.sell_prob,5),DoubleToString(chall.skip_prob,5),DoubleToString(chall.buy_prob,5),DoubleToString(chall.take_prob,5),
      chall_policy_valid?"1":"0",chall_policy_decision,chall_policy_reason,
      DoubleToString(final_score,5),decision,gate_reason,InpAllowLiveTrading?"1":"0");
   FileFlush(g_csv);
  }

int OnInit()
  {
   if(InpRiskPct<0.0 || InpSL_ATR<=0.0 || InpTP_ATR<=0.0) return INIT_PARAMETERS_INCORRECT;
   if(!ValidateTrueMtfContract())
     {
      Print("TRUE_MTF_DYNAMIC_V1 geometry validation failed.");
      return INIT_PARAMETERS_INCORRECT;
     }
   if(!StrategyOptimizerSevenFamilyContract())
     {
      Print("Strategy Optimizer V2 contract failed: optimization requires live tester execution, distinct available Confirm Symbol, and all seven family weights > 0.");
      return INIT_PARAMETERS_INCORRECT;
     }
   if(!ValidSequenceLength(InpChampionSequenceLength) || !ValidSequenceLength(InpChallengerSequenceLength) ||
      !ValidSequenceLength(InpChampionHybridSequenceLength) || !ValidSequenceLength(InpChallengerHybridSequenceLength)) return INIT_PARAMETERS_INCORRECT;
   if(!ValidateOnnxTopologyIdentity(InpChampionHybrid,InpChampionModelFamily,InpChampionHybridPolicyFamily,"Champion")) return INIT_PARAMETERS_INCORRECT;
   if(!ValidateOnnxTopologyIdentity(InpChallengerHybrid,InpChallengerModelFamily,InpChallengerHybridPolicyFamily,"Shadow")) return INIT_PARAMETERS_INCORRECT;
   InitFeatureHistory();
   if(StringLen(InpConfirmSymbol)>0 && !SymbolSelect(InpConfirmSymbol,true))
     {
      Print("Could not select MTF relative/confirm symbol ",InpConfirmSymbol);
      return INIT_FAILED;
     }
   for(int role=0;role<MTF_ROLE_COUNT;role++)
     {
      if(!InitRoleIndicators(role))
        {
         PrintFormat("MTF indicator handle creation failed role=%d tf=%s error=%d",
                     role,EnumToString(g_roleTf[role]),GetLastError());
         return INIT_FAILED;
        }
     }
   bool champ_ok=true;
   if(InpUseOnnxChampion && InpChampionHybrid)
     {
      champ_ok=InitDirectionOnnxSession(true,InpChampionTemporalModel,InpChampionHybridSequenceLength,g_onnxChampionTemporal)
               && InitHybridPolicyOnnxSession(true,InpChampionHybridPolicyModel,g_onnxChampionHybridPolicy);
     }
   else
      champ_ok=InitOnnxSession(InpUseOnnxChampion,InpChampionModel,InpChampionSequenceLength,g_onnxChampion);
   if(InpUseOnnxChampion && !champ_ok)
     {
      if(g_onnxChampionTemporal!=INVALID_HANDLE) { OnnxRelease(g_onnxChampionTemporal); g_onnxChampionTemporal=INVALID_HANDLE; }
      if(g_onnxChampionHybridPolicy!=INVALID_HANDLE) { OnnxRelease(g_onnxChampionHybridPolicy); g_onnxChampionHybridPolicy=INVALID_HANDLE; }
      if(InpOnnxFailClosed) return INIT_FAILED;
     }

   bool chall_ok=true;
   if(InpUseOnnxChallenger && InpChallengerHybrid)
     {
      chall_ok=InitDirectionOnnxSession(true,InpChallengerTemporalModel,InpChallengerHybridSequenceLength,g_onnxChallengerTemporal)
               && InitHybridPolicyOnnxSession(true,InpChallengerHybridPolicyModel,g_onnxChallengerHybridPolicy);
     }
   else
      chall_ok=InitOnnxSession(InpUseOnnxChallenger,InpChallengerModel,InpChallengerSequenceLength,g_onnxChallenger);
   if(!chall_ok)
     {
      if(g_onnxChallengerTemporal!=INVALID_HANDLE) { OnnxRelease(g_onnxChallengerTemporal); g_onnxChallengerTemporal=INVALID_HANDLE; }
      if(g_onnxChallengerHybridPolicy!=INVALID_HANDLE) { OnnxRelease(g_onnxChallengerHybridPolicy); g_onnxChallengerHybridPolicy=INVALID_HANDLE; }
      Print("Warning: Challenger ONNX unavailable; shadow inference disabled.");
     }
   bool champion_policy_ok=LoadDecisionPolicy(InpUseOnnxChampion && InpUseChampionPolicy,InpChampionPolicyFile,g_championPolicy);
   if(InpUseOnnxChampion && InpUseChampionPolicy && !champion_policy_ok && InpRequireChampionPolicy) return INIT_FAILED;
   bool challenger_policy_ok=LoadDecisionPolicy(InpUseOnnxChallenger && InpUseChallengerPolicy,InpChallengerPolicyFile,g_challengerPolicy);
   if(InpUseOnnxChallenger && InpUseChallengerPolicy && !challenger_policy_ok && InpRequireChallengerPolicy) return INIT_FAILED;
   UpdateDailyState();
   RebuildChampionPositionTracking();
   OpenTelemetry();
   OpenTradeAuditCsv();
   OpenTrainingData();
   g_trade.SetExpertMagicNumber(InpMagic);
   g_trade.SetDeviationInPoints(InpSlippagePoints);
   Print("Max TRUE MTF v2.10 initialized. Contract=",MAX_MTF_STRATEGY_CONTRACT,
         " Geometry=",EnumToString(g_roleTf[MTF_ROLE_CONTEXT]),"/",
         EnumToString(g_roleTf[MTF_ROLE_STRUCTURE]),"/",
         EnumToString(g_roleTf[MTF_ROLE_MAIN]),"/",
         EnumToString(g_roleTf[MTF_ROLE_TIMING]),
         " LiveTrading=",(InpAllowLiveTrading?"TRUE":"FALSE"),
         " ChampionONNX=",(InpUseOnnxChampion?"ON":"OFF")," ChallengerShadow=",(InpUseOnnxChallenger?"ON":"OFF"));
   return INIT_SUCCEEDED;
  }

void OnDeinit(const int reason)
  {
   for(int role=0;role<MTF_ROLE_COUNT;role++) ReleaseRoleIndicators(role);
   if(g_onnxChampion!=INVALID_HANDLE) OnnxRelease(g_onnxChampion);
   if(g_onnxChallenger!=INVALID_HANDLE) OnnxRelease(g_onnxChallenger);
   if(g_onnxChampionTemporal!=INVALID_HANDLE) OnnxRelease(g_onnxChampionTemporal);
   if(g_onnxChampionHybridPolicy!=INVALID_HANDLE) OnnxRelease(g_onnxChampionHybridPolicy);
   if(g_onnxChallengerTemporal!=INVALID_HANDLE) OnnxRelease(g_onnxChallengerTemporal);
   if(g_onnxChallengerHybridPolicy!=INVALID_HANDLE) OnnxRelease(g_onnxChallengerHybridPolicy);
   if(g_csv!=INVALID_HANDLE) { FileFlush(g_csv); FileClose(g_csv); g_csv=INVALID_HANDLE; }
   if(g_championTradesCsv!=INVALID_HANDLE) { FileFlush(g_championTradesCsv); FileClose(g_championTradesCsv); g_championTradesCsv=INVALID_HANDLE; }
   if(g_shadowTradesCsv!=INVALID_HANDLE) { FileFlush(g_shadowTradesCsv); FileClose(g_shadowTradesCsv); g_shadowTradesCsv=INVALID_HANDLE; }
  }

void OnTradeTransaction(const MqlTradeTransaction &trans,const MqlTradeRequest &request,const MqlTradeResult &result)
  {
   // v0.8.6: optimizer R fitness is still reconstructed only from complete history
   // in OnTester(). tester event ordering is not a scientific accounting authority.
   // The live Champion ledger below is audit-only and never mutates optimizer fitness
   // or execution authority.
   if(trans.type==TRADE_TRANSACTION_DEAL_ADD && trans.deal>0)
      LogChampionDealAudit(trans.deal);
  }

string StrategyOptimizerFrameInputsBlob(const ulong pass,bool &ok)
  {
   string parameters[];
   uint parameters_count=0;
   ok=FrameInputs(pass,parameters,parameters_count);
   if(!ok || parameters_count==0) return "";
   string blob="";
   for(uint i=0;i<parameters_count;i++)
     {
      if(i>0) blob+="|";
      blob+=parameters[i];
     }
   return blob;
  }

void StrategyOptimizerProcessMetricFrames()
  {
   if(g_optimizerMetricsHandle==INVALID_HANDLE) return;
   ulong pass=0;
   string name="";
   long id=0;
   double value=0.0;
   double data[];
   while(FrameNext(pass,name,id,value,data))
     {
      if(name!="MAX_R_METRICS_V2" || id!=InpOptimizerRunNonce) continue;
      if(ArraySize(data)<9)
        {
         PrintFormat("MAX_OPTIMIZER_FRAME_SCHEMA_FAIL pass=%I64u size=%d",pass,ArraySize(data));
         continue;
        }
      bool inputs_ok=false;
      string frame_inputs=StrategyOptimizerFrameInputsBlob(pass,inputs_ok);
      if(!inputs_ok || StringLen(frame_inputs)==0)
        {
         PrintFormat("MAX_OPTIMIZER_FRAME_INPUTS_FAIL pass=%I64u error=%d",pass,GetLastError());
         continue;
        }
      // FrameNext pass is an opaque ulong identity in real MT5 genetic runs.
      // Preserve it as text; never cast it through signed long/double. Python joins
      // sidecar evidence to SpreadsheetML by the exact FrameInputs parameter vector.
      FileWrite(g_optimizerMetricsHandle,
                "MAX_OPTIMIZER_METRICS_V2",
                "MAX_OPTIMIZER_FITNESS_V2",
                StringFormat("%I64u",pass),
                frame_inputs,
                value,    // custom_fitness (FrameAdd scalar)
                data[8],  // trade_exponent_alpha
                data[0],  // mean_expectancy_r
                data[1],  // weighted_r
                (long)data[2], // mt5_trades
                (long)data[3], // r_accounted_trades
                data[4],  // sum_r
                data[5],  // sum_net
                data[6],  // sum_initial_risk
                (long)data[7], // accounting_errors
                id);
      FileFlush(g_optimizerMetricsHandle);
     }
  }

int OnTesterInit()
  {
   if(StringLen(InpOptimizerMetricsFile)==0) return INIT_PARAMETERS_INCORRECT;
   if(!MathIsValidNumber(InpOptimizerTradeExponent) ||
      InpOptimizerTradeExponent<0.0 || InpOptimizerTradeExponent>1.0)
      return INIT_PARAMETERS_INCORRECT;
   FileDelete(InpOptimizerMetricsFile);
   g_optimizerMetricsHandle=FileOpen(InpOptimizerMetricsFile,FILE_WRITE|FILE_CSV|FILE_ANSI,",");
   if(g_optimizerMetricsHandle==INVALID_HANDLE)
     {
      PrintFormat("MAX_OPTIMIZER_METRICS_OPEN_FAIL file=%s error=%d",InpOptimizerMetricsFile,GetLastError());
      return INIT_FAILED;
     }
   FileWrite(g_optimizerMetricsHandle,
             "evidence_schema","fitness_schema","frame_pass_id","frame_inputs",
             "custom_fitness","trade_exponent_alpha","mean_expectancy_r","weighted_r",
             "mt5_trades","r_accounted_trades","sum_r","sum_net","sum_initial_risk",
             "accounting_errors","run_nonce");
   FileFlush(g_optimizerMetricsHandle);
   return INIT_SUCCEEDED;
  }

void OnTesterPass()
  {
   StrategyOptimizerProcessMetricFrames();
  }

void OnTesterDeinit()
  {
   StrategyOptimizerProcessMetricFrames();
   if(g_optimizerMetricsHandle!=INVALID_HANDLE)
     {
      FileFlush(g_optimizerMetricsHandle);
      FileClose(g_optimizerMetricsHandle);
      g_optimizerMetricsHandle=INVALID_HANDLE;
     }
  }

double OnTester()
  {
   long mt5_trades=(long)TesterStatistics(STAT_TRADES);
   double mean_r=-1.0e9;
   double weighted_r=-1.0e9;
   double custom_fitness=-1.0e9;
   bool rebuilt=StrategyOptimizerRebuildHistoryMetrics();
   bool valid=StrategyOptimizerSevenFamilyContract() && rebuilt;
   if(valid && g_optimizerClosedTrades>0 && g_optimizerAccountingErrors==0 &&
      mt5_trades>0 && mt5_trades==(long)g_optimizerClosedTrades &&
      g_optimizerSumRisk>0.0 && MathIsValidNumber(g_optimizerSumR) &&
      MathIsValidNumber(g_optimizerSumNet))
     {
      mean_r=g_optimizerSumR/(double)g_optimizerClosedTrades;
      weighted_r=g_optimizerSumNet/g_optimizerSumRisk;
      custom_fitness=mean_r*MathPow((double)g_optimizerClosedTrades,InpOptimizerTradeExponent);
      if(!MathIsValidNumber(mean_r) || !MathIsValidNumber(weighted_r) ||
         !MathIsValidNumber(custom_fitness) ||
         !MathIsValidNumber(InpOptimizerTradeExponent) ||
         InpOptimizerTradeExponent<0.0 || InpOptimizerTradeExponent>1.0)
        {
         mean_r=-1.0e9;
         weighted_r=-1.0e9;
         custom_fitness=-1.0e9;
         valid=false;
        }
     }
   else
     {
      PrintFormat("MAX_EXPECTANCY_R_FAIL_CLOSED mt5_trades=%d r_accounted=%d accounting_errors=%d",
                  mt5_trades,g_optimizerClosedTrades,g_optimizerAccountingErrors);
      valid=false;
     }

   // Native MT5 Custom/Result is optimizer search guidance only. Mean R and
   // Weighted R remain independent R-accounting evidence and Python KPI gates.
   // alpha=0 -> Mean R; alpha=0.5 -> Mean R*sqrt(trades);
   // alpha=1 -> cumulative Sum R because mean_r*closed_trades == sum_r.
   double metrics[9];
   metrics[0]=mean_r;
   metrics[1]=weighted_r;
   metrics[2]=(double)mt5_trades;
   metrics[3]=(double)g_optimizerClosedTrades;
   metrics[4]=g_optimizerSumR;
   metrics[5]=g_optimizerSumNet;
   metrics[6]=g_optimizerSumRisk;
   metrics[7]=(double)g_optimizerAccountingErrors;
   metrics[8]=InpOptimizerTradeExponent;
   if(MQLInfoInteger(MQL_OPTIMIZATION))
     {
      if(!FrameAdd("MAX_R_METRICS_V2",InpOptimizerRunNonce,custom_fitness,metrics))
        {
         PrintFormat("MAX_OPTIMIZER_FRAME_ADD_FAIL nonce=%I64d error=%d",InpOptimizerRunNonce,GetLastError());
         return -1.0e9;
        }
     }
   return valid ? custom_fitness : -1.0e9;
  }

void OnTick()
  {
   UpdateDailyState();
   if(InpOneDecisionPerBar && !IsNewBar()) return;

   datetime decision_time=iTime(_Symbol,g_roleTf[MTF_ROLE_MAIN],0);
   if(decision_time<=0) return;

   MqlRates context_rates[],structure_rates[],main_rates[],timing_rates[];
   MarketSnapshot context_s,structure_s,main_s,timing_s;
   if(!BuildRoleSnapshot(MTF_ROLE_CONTEXT,decision_time,context_s,context_rates)) return;
   if(!BuildRoleSnapshot(MTF_ROLE_STRUCTURE,decision_time,structure_s,structure_rates)) return;
   if(!BuildRoleSnapshot(MTF_ROLE_MAIN,decision_time,main_s,main_rates)) return;
   if(!BuildRoleSnapshot(MTF_ROLE_TIMING,decision_time,timing_s,timing_rates)) return;

   FamilySignal context_f[],structure_f[],main_f[],timing_f[],families[];
   EvaluateFamiliesForRole(context_s,MTF_ROLE_CONTEXT,decision_time,context_f);
   EvaluateFamiliesForRole(structure_s,MTF_ROLE_STRUCTURE,decision_time,structure_f);
   EvaluateFamiliesForRole(main_s,MTF_ROLE_MAIN,decision_time,main_f);
   EvaluateFamiliesForRole(timing_s,MTF_ROLE_TIMING,decision_time,timing_f);
   FuseFamilies(context_f,structure_f,main_f,timing_f,families);
   double consensus=0.0;
   double rule_score=RuleMetaScore(families,consensus);

   double features[];
   BuildFeatures(main_s,families,rule_score,features);
   LogTrainingRow(main_s,features,consensus);
   PushFeatureHistory(features);
   OnnxPrediction champ=EmptyPrediction();
   OnnxPrediction chall=EmptyPrediction();
   if(InpUseOnnxChampion)
      champ=(InpChampionHybrid ? RunHybridOnnx(g_onnxChampionTemporal,g_onnxChampionHybridPolicy,features,InpChampionHybridSequenceLength)
                               : RunOnnx(g_onnxChampion,features,InpChampionSequenceLength));
   if(InpUseOnnxChallenger)
      chall=(InpChallengerHybrid ? RunHybridOnnx(g_onnxChallengerTemporal,g_onnxChallengerHybridPolicy,features,InpChallengerHybridSequenceLength)
                                 : RunOnnx(g_onnxChallenger,features,InpChallengerSequenceLength));

   double final_score=rule_score;
   string gate_reason="";
   bool gate_ok=TradingGate(main_s,consensus,gate_reason);

   if(InpUseOnnxChampion)
     {
      if(!champ.valid)
        {
         if(InpOnnxFailClosed) { gate_ok=false; gate_reason="ONNX_FAIL_CLOSED"; }
        }
      else
        {
         double blend=Clamp01(InpOnnxBlend);
         final_score=(1.0-blend)*rule_score+blend*champ.directional;
         if(InpUseChampionPolicy && g_championPolicy.valid)
           {
            int pd=0; string pr="";
            if(!EvaluatePolicyDecision(champ,g_championPolicy,main_s,rule_score,consensus,pd,pr)) { gate_ok=false; gate_reason=pr; }
           }
         else if(champ.take_prob<InpMinOnnxTakeProb) { gate_ok=false; gate_reason="ONNX_SKIP"; }
         if(InpRequireOnnxAgreement && Sign(rule_score)!=0.0 && Sign(champ.directional)!=0.0 && Sign(rule_score)!=Sign(champ.directional))
           { gate_ok=false; gate_reason="ONNX_DISAGREEMENT"; }
        }
     }

   final_score=Clamp(final_score,-1.0,1.0);
   ManagePosition(final_score);

   string decision="SKIP";
   if(gate_ok && MathAbs(final_score)>=InpEntryThreshold)
     {
      int direction=(final_score>0.0?1:-1);
      if(AnyPositionOnSymbol()) decision="SKIP_POSITION_EXISTS";
      else
        {
         decision=(direction>0?"TAKE_BUY":"TAKE_SELL");
         if(InpAllowLiveTrading) OpenTrade(direction,main_s,final_score);
        }
     }

   int chall_policy_dir=0; string chall_policy_reason="POLICY_OFF"; bool chall_policy_take=false;
   string chall_policy_decision="SKIP";
   if(InpUseOnnxChallenger && InpUseChallengerPolicy)
     {
      chall_policy_take=EvaluatePolicyDecision(chall,g_challengerPolicy,main_s,rule_score,consensus,chall_policy_dir,chall_policy_reason);
      if(chall_policy_take) chall_policy_decision=(chall_policy_dir>0?"TAKE_BUY":"TAKE_SELL");
     }
   else if(InpUseOnnxChallenger && chall.valid && chall.take_prob>=InpMinOnnxTakeProb)
     {
      chall_policy_dir=(chall.buy_prob>=chall.sell_prob?1:-1);
      chall_policy_take=true; chall_policy_reason="ONNX_THRESHOLD_NO_POLICY";
      chall_policy_decision=(chall_policy_dir>0?"TAKE_BUY":"TAKE_SELL");
     }
   if(chall_policy_take) LogShadowTradeAudit(main_s,chall,chall_policy_dir,chall_policy_reason,rule_score,consensus);
   LogTelemetry(main_s,families,rule_score,consensus,champ,chall,g_challengerPolicy.valid,chall_policy_decision,chall_policy_reason,final_score,decision,gate_reason);

   string champ_text="OFF/INVALID";
   if(champ.valid) champ_text=StringFormat("SELL %.2f SKIP %.2f BUY %.2f",champ.sell_prob,champ.skip_prob,champ.buy_prob);
   string chall_text="OFF/INVALID";
   if(chall.valid) chall_text=StringFormat("SELL %.2f SKIP %.2f BUY %.2f",chall.sell_prob,chall.skip_prob,chall.buy_prob);
   string gate_text="";
   if(StringLen(gate_reason)>0) gate_text=" | Gate: "+gate_reason;

   Comment("Max\n",
           "Live: ",(InpAllowLiveTrading?"ON":"OFF (shadow/signal)"),"\n",
           "Rule score: ",DoubleToString(rule_score,3)," | Final: ",DoubleToString(final_score,3)," | Consensus: ",DoubleToString(consensus,2),"\n",
           "Champion: ",champ_text,"\n",
           "Challenger shadow: ",chall_text,"\n",
           "Challenger policy: ",chall_policy_decision," (",chall_policy_reason,")\n",
           "Decision: ",decision,gate_text);
  }
//+------------------------------------------------------------------+
