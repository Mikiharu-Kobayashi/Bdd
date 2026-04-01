# -*- coding: utf-8 -*-
import streamlit as st
import google.generativeai as genai
import pandas as pd
import plotly.express as px
import json
import re
import requests
from docx import Document
import io
import time

# --- ページ設定 ---
st.set_page_config(page_title="Quick BDD Analyzer", layout="wide")
st.title("Quick BDD（単一事業向け - FMP API版）")

# --- サイドバー：APIキー設定 ---
with st.sidebar:
    st.header("Settings")
    
    st.markdown("### 1. Gemini API Key")
    api_key = st.text_input("Enter Gemini API Key", type="password")
    
    if api_key:
        genai.configure(api_key=api_key)
        try:
            available_models = [m.name for m in genai.list_models() if 'generateContent' in m.supported_generation_methods]
            selected_model = next((m for m in available_models if 'flash' in m), available_models[0])
            st.success(f"Model Active: {selected_model}")
        except Exception:
            selected_model = "gemini-1.5-flash"
            st.warning(f"Using default model: {selected_model}")
    else:
        st.info("APIキーを入力すると分析が開始できます")

    st.markdown("### 2. FMP API Key")
    st.markdown("[FMP公式サイト](https://site.financialmodelingprep.com/developer/docs/dashboard)から取得した無料APIキーを入力してください")
    fmp_api_key = st.text_input("Enter FMP API Key", type="password")

# --- Word生成用の関数 ---
def create_word(target, description, report_text):
    doc = Document()
    doc.add_heading('Strategic BDD Report', 0)
    doc.add_heading(f'Target Analysis: {target}', level=1)
    doc.add_paragraph(f"Description: {description}")
    doc.add_heading('Analysis Results', level=1)
    doc.add_paragraph(report_text)
    
    bio = io.BytesIO()
    doc.save(bio)
    return bio.getvalue()

# --- FMP APIから財務データを取得する関数（エラー検知強化版） ---
@st.cache_data(ttl=86400, show_spinner=False)
def fetch_fmp_financials(ticker_code, fmp_key):
    # FMPの制限対策（念のため少し待機）
    time.sleep(0.5)
    
    # 4桁の数字のみの場合は、日本株指定のために.Tを付与する
    raw_ticker = str(ticker_code).strip()
    if re.match(r'^\d{4}$', raw_ticker):
        ticker = f"{raw_ticker}.T"
    else:
        ticker = raw_ticker

    base_url = "https://financialmodelingprep.com/api/v3"
    params = {"apikey": fmp_key}
    
    try:
        # 1. クォート情報（時価総額など）
        quote_res = requests.get(f"{base_url}/quote/{ticker}", params=params).json()
        # 2. 損益計算書（過去5年分）
        is_res = requests.get(f"{base_url}/income-statement/{ticker}", params={"limit": 5, "apikey": fmp_key}).json()
        # 3. 貸借対照表（過去5年分）
        bs_res = requests.get(f"{base_url}/balance-sheet-statement/{ticker}", params={"limit": 5, "apikey": fmp_key}).json()
        # 4. 主要指標（直近のROEなど）
        metrics_res = requests.get(f"{base_url}/key-metrics-ttm/{ticker}", params=params).json()

        # 💡 【改善】FMP APIからのエラーメッセージ（辞書型）をキャッチして例外を投げる
        if isinstance(quote_res, dict) and "Error Message" in quote_res:
            raise ValueError(f"APIエラー: {quote_res['Error Message']}")
        if isinstance(is_res, dict) and "Error Message" in is_res:
            raise ValueError(f"APIエラー: {is_res['Error Message']}")

        # 💡 【改善】空のリストが返ってきた場合（企業が存在しない等）の対応
        if not quote_res or not is_res:
            raise ValueError(f"データが空です。ティッカーコードが誤っているか、FMP非対応の可能性があります。")

        latest_is = is_res[0]
        quote = quote_res[0]
        metrics = metrics_res[0] if metrics_res else {}

        val_market_cap = quote.get('marketCap', 0)
        val_revenue = latest_is.get('revenue', 0)
        val_op_income = latest_is.get('operatingIncome', 0)
        val_roe = metrics.get('roeTTM', 0)

        # ポジショニングマップ用の最新サマリー
        summary = {
            "時価総額(億)": round(val_market_cap / 1e8, 1) if val_market_cap else 0,
            "売上高(億)": round(val_revenue / 1e8, 1) if val_revenue else 0,
            "営業利益率(%)": round((val_op_income / val_revenue) * 100, 1) if val_revenue and val_revenue != 0 else 0,
            "ROE(%)": round(val_roe * 100, 1) if val_roe else 0
        }

        # AI分析用の5年分ヒストリカルデータ整形
        df_is = pd.DataFrame(is_res)
        df_bs = pd.DataFrame(bs_res)

        hist_text = "【損益計算書 (主要項目)】\n"
        if not df_is.empty:
            is_cols = [c for c in ['date', 'revenue', 'operatingIncome', 'netIncome', 'sellingGeneralAndAdministrativeExpenses'] if c in df_is.columns]
            hist_text += df_is[is_cols].to_string(index=False) + "\n"
        else:
            hist_text += "データなし\n"

        hist_text += "\n【貸借対照表 (主要項目)】\n"
        if not df_bs.empty:
            bs_cols = [c for c in ['date', 'totalAssets', 'totalStockholdersEquity', 'inventory', 'accountReceivables', 'accountPayables'] if c in df_bs.columns]
            hist_text += df_bs[bs_cols].to_string(index=False) + "\n"
        else:
            hist_text += "データなし\n"

        return summary, hist_text

    except Exception as e:
        raise ValueError(f"データ取得エラー ({ticker}): {str(e)}")


# --- 1. 競合特定フェーズ ---
with st.form(key='search_form'):
    target_name = st.text_input("分析したい企業の名前を入力してください", "")
    submit_button = st.form_submit_button(label='分析開始')

if submit_button:
    if not api_key:
        st.error("左上の矢印 >> からサイドバーを開き、Gemini APIキーを入力してください")
    elif not target_name:
        st.warning("企業名を入力してください")
    else:
        model = genai.GenerativeModel(selected_model)
        with st.spinner(f"🔍 {target_name} を調査中..."):
            comp_prompt = f"""
            「{target_name}」のBDDを行います。以下をJSON形式のみで出力してください。
            {{
              'description': '対象企業の概要',
              'competitors': [
                {{'name': '企業名', 'ticker': '銘柄コード.T', 'reason': '競合となりうる理由(30文字以内)'}}
              ]
            }}
            """
            try:
                res = model.generate_content(comp_prompt)
                match = re.search(r'\{.*\}', res.text, re.DOTALL)
                if match:
                    data = json.loads(match.group())
                    st.session_state.target_desc = data['description']
                    st.session_state.all_competitors = data['competitors']
                    st.session_state.step = 2
                else:
                    st.error("AIからの応答を解析できませんでした。もう一度お試しください。")
            except Exception as e:
                st.error(f"AIによる調査でエラーが発生しました: {e}")

# --- 2. 競合選択 & 財務分析フェーズ ---
if "step" in st.session_state and st.session_state.step >= 2:
    st.subheader(f"対象企業の概要: {target_name}")
    st.info(st.session_state.target_desc)

    st.subheader("分析対象の選択")
    st.write("AIが特定した競合候補です。分析に含める企業を選択してください。")

    comp_list = st.session_state.all_competitors
    selected_tickers = []  
    
    for comp in comp_list:
        if st.checkbox(f"**{comp['name']}** ({comp['ticker']}) — {comp['reason']}", value=True, key=f"check_{comp['ticker']}"):
            selected_tickers.append(comp['ticker'])

    if st.button("選択した企業で詳細分析・レポート生成", key="exec_analysis"):
        final_competitors = [c for c in comp_list if c['ticker'] in selected_tickers]
        
        if not final_competitors:
            st.error("少なくとも1社は選択してください。")
        elif not fmp_api_key:
            st.error("左上の矢印 >> からサイドバーを開き、FMP APIキーを入力してください")
        else:
            model = genai.GenerativeModel(selected_model)
            with st.spinner("📡 FMP APIから5年分の詳細財務データを抽出中..."):
                summary_results = []
                detailed_financials_for_ai = ""
                error_targets = []
                
                for comp in final_competitors:
                    try:
                        # FMP関数呼び出し
                        summary, hist_text = fetch_fmp_financials(comp['ticker'], fmp_api_key)
                        
                        summary["企業名"] = comp['name']
                        summary_results.append(summary)

                        detailed_financials_for_ai += f"\n--- {comp['name']} ({comp['ticker']}) 過去5年分財務データ ---\n"
                        detailed_financials_for_ai += hist_text
                        detailed_financials_for_ai += "\n"

                    except Exception as e:
                        error_targets.append(str(e))
                        continue
                
                if error_targets:
                    st.warning("一部のデータ取得に制限がありました:\n\n" + "\n\n".join(error_targets))

                if not summary_results:
                    st.error("財務データの取得にすべて失敗しました。")
                else:
                    # 最新データの表示（表）
                    df = pd.DataFrame(summary_results)
                    # 列の並び順調整
                    df = df[["企業名", "時価総額(億)", "売上高(億)", "営業利益率(%)", "ROE(%)"]]
                    st.subheader("競合の主要財務数値（最新）")
                    st.dataframe(df.style.format(precision=1), use_container_width=True)
            
                    # 最新データのビジュアル化（ポジショニングマップ）
                    st.subheader("競合ポジショニングマップ（最新）")
                    plot_df = df.copy()
                    plot_df["表示サイズ"] = plot_df["時価総額(億)"].apply(lambda x: 0.1 if x <= 0 else x)
            
                    fig = px.scatter(
                        plot_df, x="営業利益率(%)", y="ROE(%)", size="表示サイズ", 
                        color="企業名", text="企業名", template="plotly_white",
                        labels={"表示サイズ": "時価総額(億)"}
                    )
                    fig.update_layout(paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)', font=dict(color="white"))
                    st.plotly_chart(fig, use_container_width=True)
                    
                    # 4. BDDレポート生成
                    with st.spinner("📝 市場分析およびバリューアップ仮説を生成中..."):
                        report_prompt = f"""
                        あなたは、トップティアの戦略コンサルティングファーム出身で、現在は大手PEファンドの投資委員（ICメンバー）です。
                        「{target_name}」のBDD（ビジネス・デューデリジェンス）において、特に「アップサイド・ポテンシャル」と「バリューアップ計画（VCP）」に焦点を当てた投資判断資料を作成してください。

                        与えられた財務データ（{detailed_financials_for_ai}）を起点に、一般的な市場解説ではなく、「この会社は、具体的にどのレバーを引けば企業価値（EV）が2倍、3倍になるか？」という視点で、ドライかつ論理的に、そして商品名やECサイト名称、組織名などの固有名詞を用いながら対象会社を具体的に記述してください。
                        アウトプットは章の名から簡潔に始めてください。
                        内容を深めるため、BDD/ファンドなどの単語を書いていますがOutputには含めず、対象企業の純粋な分析レポートとして様々な用途に対応できるようにしてください。投資判断に限定しないでください。
                        
                        【読みやすさの厳格ルール】
                        1. 構造化：## で章を、### で節を区切り、情報の階層を明確にすること。
                        2. 視覚化：重要な数値や結論は **太字** で強調し、3つ以上の項目は必ず箇条書きにすること。ただし、Word出力時にAIライクにならないよう *の多用は避ける
                        3. 簡潔性：1文を短くし、専門用語には平易な注釈を添えること。
                        4. 要約：各章の冒頭に「> 結論：(一行要約)」を記述すること。
                        
                        【レポート構成】
                        1. エグゼクティブ・サマリー：
                           業界全体のファンダメンタルズ/競合優位性/ {target_name}の現状および強みを踏まえた成長戦略。
                           1-1.事業戦略
                           1-2.人事戦略
                           1-3.財務戦略
                        2. 市場分析
                           2-1.現状
                               市場規模（TAM/SAM/SOMの推計）
                               市場の成長率
                               市場の利益率
                               競争の激化要因（価格競争か、機能競争か）
                           2-2.将来性
                               足元成長を後押ししているトレンド/成長率の高いセグメント（顧客層/価格帯など）の最新トピック
                               現在および将来のマクロ環境（PEST）が与えるインパクト      
                        3. 競合分析
                            3-1.各社の特徴
                                バリューチェーン
                                ターゲット顧客/ポジショニング
                                マーケティング戦略（product/price/place/promotion)
                            3-2.各社の財務分析
                                収益性の源泉とコスト構造の解剖
                                    限界利益率/オペレーティング・レバレッジ（売上増がどれだけ利益増に直結するか）
                                    利益の質（広告宣伝費や販促費の比率から、ブランド力による「指名買い」なのか「販促による押し込み」なのか）
                                    ユニットエコノミクス推論（財務数値から、各社の顧客獲得コストやLTVといった事業推進のカギとなる値の推定）
                                デュポン分析的視点
                                    ROEを「売上高純利益率 × 総資産回転率 × 財務レバレッジ」の要素に分解し、競合との差を特定
                                    差を生む構造的要因（コスト構造、アセットライトモデル等）の解剖
                                CCC (キャッシュ・コンバージョン・サイクル):
                                    売上債権・棚卸資産の回転期間から、各社の運転資本（Working Capital）の管理能力と、成長に伴う資金需要の強さを推測
                        4. {target_name}への戦略的提言
                           {target_name}の情報が少ない場合、公開情報から推計して記載してください。～と仮定します、などは都度書く必要はありません
                           4-1.マーケットと競合、  {target_name}の公開データから取りうる戦略オプションの洗い出し
                               トップラインの強化: 新規顧客開拓、価格決定権の行使（値上げ）、新商材展開の余地。
                               コストの最適化: 競合比較から見える、コスト削減の余地（DXによる販管費削減、サプライチェーン見直し）。
                           4-2.数年で2-3倍の収益を目指す目線での仮評価
                                現在の時価総額・マルチプルの高さが、どの指標（売上成長率か、それともROEか）に最も強く相関しているかを特定し
                                どのようなレバー（出店加速、単価アップ等）を引けば企業価値が最大化するか
                        5. 事業推進上の致命的リスク（Red Flags）:
                           最悪のシナリオ（法規制、強力な新規参入、技術的陳腐化）
                           それに対する具体的な緩和策（Mitigation Plan）
                    
                        プロフェッショナルな論調で、具体的数値に基づいた示唆を出してください。
                        """
                        
                        try:
                            # レポート生成実行
                            report_response = model.generate_content(report_prompt)
                            report_content = report_response.text
                            
                            # 画面にレポートを表示
                            st.divider()
                            st.markdown(report_content)
                            
                            # --- Word出力セクション ---
                            st.markdown("---")
                            try:
                                # session_state から企業概要を取得
                                desc_text = st.session_state.get('target_desc', '概要なし')
                                
                                # Wordファイルの生成
                                word_data = create_word(target_name, desc_text, report_content)
                                
                                st.download_button(
                                    label="📝 Word形式で保存",
                                    data=word_data,
                                    file_name=f"Quick_BDD_Report_{target_name}.docx",
                                    mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                                    key="word_download"
                                )
                            except Exception as word_err:
                                st.error(f"Word生成中にエラーが発生しました: {word_err}")
                    
                        except Exception as api_err:
                            st.error(f"レポート生成中にAIエラーが発生しました: {api_err}")
