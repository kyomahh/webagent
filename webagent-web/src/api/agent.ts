// src/api/agent.ts
import axios from 'axios';

const API_BASE = 'http://localhost:8000';

export interface Citation {
    citation_id?: string;
    title?: string;
    source?: string;
    quote?: string;
    [key: string]: unknown;
}

export interface AgentCase {
    id?: string;
    scenario_id?: string;
    name?: string;
    scenario_name?: string;
    type?: string;
    feature_id?: string;
    documents?: string[];
    citations?: Citation[];
    steps?: string[];
    expectations?: string[];
    [key: string]: unknown;
}

export interface CasesResponse {
    path?: string | null;
    count: number;
    cases: AgentCase[];
}

export interface VerificationSummaryItem {
    scenario_id: string;
    scenario_name: string;
    status: 'passed' | 'failed' | 'ignored';
    passed: boolean;
    reason: string;
}

export interface SummaryResponse {
    test_cases?: {
        count: number;
        path?: string | null;
    };
    verification?: {
        expected_count: number;
        verified_count: number;
        passed_count: number;
        failed_count: number;
        ignored_count: number;
        raw_passed_count?: number;
        raw_failed_count?: number;
        unverified_count?: number;
        pass_rate: number | null;
        items: VerificationSummaryItem[];
    };
    screenshots?: {
        count: number;
    };
    latest_report?: {
        path?: string | null;
        exists?: boolean;
    } | null;
}

export interface ScreenshotItem {
    filename: string;
    path: string;
    url: string;
    scenario_id?: string | null;
    scenario_name?: string;
    status?: string | null;
    name?: string;
    step?: number | null;
    size?: number;
    mtime?: number;
}

export interface ScreenshotsResponse {
    count: number;
    screenshots: string[];
    items: ScreenshotItem[];
}

export interface LogsResponse {
    content: string;
    offset: number;
    exists: boolean;
    path?: string | null;
    size?: number;
}

export interface RunStatusResponse {
    status: string;
    pid?: number | null;
    returncode?: number | null;
    started_at?: number | null;
    log_size?: number;
    log_mtime?: number | null;
    log_idle_seconds?: number | null;
}

export type StartMode = 'resume' | 'full';

export interface StartTestOptions {
    cases?: string[];
    mode?: StartMode;
}

export const agentApi = {
    // 新增：获取用例列表
    getCases: () => axios.get<CasesResponse | AgentCase[]>(`${API_BASE}/api/cases`),
    // 修改：发送选中的用例 ID 给后端
    startTest: (options: StartTestOptions) => axios.post(`${API_BASE}/api/start-test`, options),
    getRunStatus: () => axios.get<RunStatusResponse>(`${API_BASE}/api/run/status`),
    getLogs: (offset: number) => axios.get<LogsResponse>(`${API_BASE}/api/logs`, { params: { offset } }),
    getScreenshots: (since?: number) => axios.get<ScreenshotsResponse>(`${API_BASE}/api/screenshots`, {
        params: since ? { since } : {},
    }),
    getSummary: () => axios.get<SummaryResponse>(`${API_BASE}/api/summary`),
};

export const getImageUrl = (path: string) => `${API_BASE}${path}`;
