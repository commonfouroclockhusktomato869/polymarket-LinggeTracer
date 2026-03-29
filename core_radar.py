# ==============================================================================
# Copyright (C) 2026 领哥大虾 (lingge66). All Rights Reserved.
# Project: polymarket-LinggeTracer
# 
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
# 
# 警告：本项目采用 GPLv3 传染性开源协议。任何企业或个人未经授权，
# 严禁将本核心逻辑（及其衍生算法）用于闭源商业化盈利项目。违者必究。
# ==============================================================================
# core_radar.py
import os
import time
import requests
import pandas as pd
import logging

logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger(__name__)

class PolymarketAnalyzer:
    def __init__(self, proxy_port=None):
        self.data_url = "https://data-api.polymarket.com"
        self.lb_url = "https://lb-api.polymarket.com"
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
        
        if proxy_port:
            os.environ['http_proxy'] = f'http://127.0.0.1:{proxy_port}'
            os.environ['https_proxy'] = f'http://127.0.0.1:{proxy_port}'
            os.environ['all_proxy'] = f'socks5://127.0.0.1:{proxy_port}'
            self.session.proxies.update({
                'http': f'http://127.0.0.1:{proxy_port}',
                'https': f'http://127.0.0.1:{proxy_port}'
            })

    # 1. 解析地址
    def resolve_target(self, target, progress_callback=None):
        target = target.strip()
        if target.startswith("0x") and len(target) == 42: return target
        if progress_callback: progress_callback(f"🔎 正在排行榜寻找用户名 [{target}]...")
        for offset in [0, 500, 1000]:
            try:
                data = self.session.get(f"{self.lb_url}/profit?window=all&limit=500&offset={offset}", timeout=5).json()
                for user in data:
                    if target.lower() in str(user.get('name', '')).lower() or target.lower() in str(user.get('pseudonym', '')).lower():
                        return user.get('proxyWallet')
            except: pass
        return None

    # 2. 获取多维度真实 PnL
    def fetch_real_pnl(self, address):
        pnl_data = {"all": 0, "7d": 0, "30d": 0}
        for window in ["all", "7d", "30d"]:
            try:
                resp = self.session.get(f"{self.lb_url}/profit?window={window}&address={address}", timeout=5).json()
                if resp and isinstance(resp, list) and len(resp) > 0:
                    pnl_data[window] = resp[0].get("amount", 0) / 100 # 转为美元
            except: pass
        return pnl_data

    # 3. 抓取持仓面板 (精准算胜率、未平仓、胜负榜)
    def fetch_positions(self, address, progress_callback=None):
        if progress_callback: progress_callback("📂 正在拉取用户持仓面板 (Positions)...")
        positions = []
        offset = 0
        while True:
            url = f"{self.data_url}/positions?user={address}&sizeThreshold=0&limit=100&offset={offset}"
            try:
                resp = self.session.get(url, timeout=10).json()
                if not resp: break
                positions.extend(resp)
                if len(resp) < 100: break
                offset += 100
            except: break
        return positions

    # 4. 突破 500 限制的无限分页抓取 (Activity 接口，带死循环熔断与去重)
    def fetch_activity_history(self, address, progress_callback=None):
        all_activities = []
        url = f"{self.data_url}/activity?user={address}&limit=500"
        
        try:
            resp = self.session.get(url, timeout=10).json()
            if not resp: return all_activities
            all_activities.extend(resp)
            
            last_timestamp = resp[-1].get("timestamp")
            page = 1
            
            while len(resp) == 500 and last_timestamp:
                page += 1
                if progress_callback: 
                    progress_callback(f"⏳ 突破分页限制中... 已抓取 {len(all_activities)} 笔链上动作 (第 {page} 页) 📥")
                time.sleep(0.5)
                
                next_url = f"{self.data_url}/activity?user={address}&limit=500&end={last_timestamp}"
                resp = self.session.get(next_url, timeout=10).json()
                
                if not resp: break
                
                all_activities.extend(resp)
                
                # 💡【核心破局点 1】：防死循环，强行拨表
                new_last_timestamp = resp[-1].get("timestamp")
                if str(new_last_timestamp) == str(last_timestamp):
                    # API 卡在同一秒了！强行将时间戳减 1 秒，跳出泥潭
                    last_timestamp = str(int(last_timestamp) - 1)
                else:
                    last_timestamp = new_last_timestamp
                
        except Exception as e:
            pass
            
        # 💡【核心破局点 2】：边缘重叠数据去重
        unique_activities = []
        seen_keys = set()
        
        for act in all_activities:
            # 联合主键：交易哈希 + 动作类型 + 发生金额 + 标的市场
            tx_hash = act.get("transactionHash", "")
            act_type = act.get("type", "")
            size = str(act.get("usdcSize", ""))
            slug = act.get("slug", "")
            
            unique_key = f"{tx_hash}_{act_type}_{size}_{slug}"
            
            if unique_key not in seen_keys:
                seen_keys.add(unique_key)
                unique_activities.append(act)
                
        return unique_activities

    def generate_ai_summary(self, target_input, progress_callback=None):
        wallet_address = self.resolve_target(target_input, progress_callback)
        if not wallet_address: return f"❌ 未能找到 [{target_input}] 的钱包。"

        # 获取三大核心数据
        pnl = self.fetch_real_pnl(wallet_address)
        positions = self.fetch_positions(wallet_address, progress_callback)
        activities = self.fetch_activity_history(wallet_address, progress_callback)

        if not activities: return "抓取成功，但该钱包无交易活动。"

        if progress_callback: progress_callback("🧠 数据矩阵构建完毕，正在提取高阶特征...")

        # --- 特征工程 1：持仓与胜率精确计算 ---
        won, lost, open_count = 0, 0, 0
        total_open_value = 0
        settled_pnl_list = []
        
        for pos in positions:
            is_redeemable = pos.get("redeemable", False)
            current_value = pos.get("currentValue", 0)
            cash_pnl = pos.get("cashPnl", 0)
            
            if is_redeemable and current_value > 0: won += 1
            elif is_redeemable and current_value == 0: lost += 1
            elif not is_redeemable and current_value > 0: 
                open_count += 1
                total_open_value += current_value
                
            if is_redeemable:
                settled_pnl_list.append({"market": pos.get("title"), "pnl": cash_pnl})
                
        win_rate = (won / (won + lost) * 100) if (won + lost) > 0 else 0
        
        # 胜负榜 Top 3
        settled_pnl_list.sort(key=lambda x: x["pnl"], reverse=True)
        top_wins = settled_pnl_list[:3]
        top_losses = settled_pnl_list[-3:] if len(settled_pnl_list) >= 3 else []

        # --- 特征工程 2：完整活动细分 (解决只看 Buy/Sell 的痛点) ---
        stats = {"TRADE_BUY": 0, "TRADE_SELL": 0, "SPLIT": 0, "MERGE": 0, "REDEEM": 0}
        total_vol = 0
        market_slugs = set()
        
        for act in activities:
            a_type = act.get("type", "")
            vol = act.get("usdcSize", 0)
            side = act.get("side", "")
            market_slugs.add(act.get("slug", ""))
            
            total_vol += vol
            if a_type == "TRADE":
                key = f"TRADE_{side}"
                if key in stats: stats[key] += 1
            elif a_type in stats:
                stats[a_type] += 1

        # 组装超级摘要给大模型
        summary = f"""
### Polymarket 大户 [{target_input}] (地址: {wallet_address[:8]}...) 终极链上档案：

#### 💰 财务与 PnL 概览
- **总历史盈亏 (All-time PnL)**: ${pnl['all']:,.2f}
- **近30天盈亏 (30d PnL)**: ${pnl['30d']:,.2f}
- **近7天盈亏 (7d PnL)**: ${pnl['7d']:,.2f}
- **当前未平仓头寸**: {open_count} 个 (总市值预估 ${total_open_value:,.2f})

#### ⚔️ 绝对胜率与胜负榜 (基于真实结算)
- **真实结算胜率**: {win_rate:.1f}% (赢 {won} 次 / 输 {lost} 次)
- **生涯最佳战役 (Top Wins)**: {', '.join([f"[{w['market']}]赚${w['pnl']:.0f}" for w in top_wins])}
- **生涯最惨战役 (Top Losses)**: {', '.join([f"[{l['market']}]亏${l['pnl']:.0f}" for l in top_losses])}

#### 📈 高阶活动细分 (无限分页全量抓取)
- **链上动作总计**: {len(activities)} 笔
- **绝对资金吞吐量**: ${total_vol:,.2f}
- **参与的独立市场数**: {len(market_slugs)} 个
- **操作类型分布**: 
  - 买入开仓 (TRADE_BUY): {stats['TRADE_BUY']} 笔
  - 卖出平仓 (TRADE_SELL): {stats['TRADE_SELL']} 笔
  - 拆分做市 (SPLIT): {stats['SPLIT']} 笔
  - 合并套利 (MERGE): {stats['MERGE']} 笔
  - 到期兑付 (REDEEM): {stats['REDEEM']} 笔
"""
        return summary
