import React from 'react';
import { HealthResponse } from '../types';

interface HeaderProps {
  health: HealthResponse | null;
  isWakingUp: boolean;
  activeTab: 'ask' | 'check';
  setActiveTab: (tab: 'ask' | 'check') => void;
}

export const Header: React.FC<HeaderProps> = ({
  health,
  isWakingUp,
  activeTab,
  setActiveTab,
}) => {
  const versions = health?.guideline_versions || {};
  const budget = health?.budget;

  // Format budget metrics
  const limit = budget?.limit ?? 100000;
  const used = budget?.used ?? 0;
  const remaining = budget?.remaining !== null && budget?.remaining !== undefined
    ? budget.remaining
    : Math.max(0, limit - used);

  const percentUsed = Math.min(100, Math.round((used / limit) * 100));
  const isLowBudget = remaining < limit * 0.1;

  return (
    <header className="header-container">
      <div className="header-top">
        <div className="title-area">
          <h1>MyInvois Compliance Copilot</h1>
          <p className="subtitle">
            Grounded Malaysian LHDN e-Invoice compliance assistant
          </p>
        </div>

        {health && health.db === 'ok' && (
          <div className="budget-meter-container" title={`Used ${used.toLocaleString()} of ${limit.toLocaleString()} tokens`}>
            <div className="budget-header">
              <span>Token Budget</span>
              <span>{percentUsed}%</span>
            </div>
            <div className="budget-progress-bar">
              <div
                className={`budget-progress-fill ${isLowBudget ? 'low' : ''}`}
                style={{ width: `${percentUsed}%` }}
              />
            </div>
            <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: '2px', color: 'var(--muted-light)', fontSize: '0.68rem' }}>
              <span>{remaining.toLocaleString()} left</span>
              <span>cap {limit.toLocaleString()}</span>
            </div>
          </div>
        )}
      </div>

      {isWakingUp && (
        <div className="cold-start-banner" role="status" aria-live="polite">
          <span className="loading-dot" />
          <div>
            <strong>Waking up, ~35s</strong> &mdash; Container is spinning up from idle on Azure Container Apps. The Ask assistant will be enabled once the server is ready.
          </div>
        </div>
      )}

      <div className="versions-bar">
        <span className="versions-label">Grounded in:</span>
        {Object.keys(versions).length > 0 ? (
          Object.entries(versions).map(([docKey, ver]) => {
            let label = docKey;
            if (docKey === 'general_guideline') label = 'General Guideline';
            else if (docKey === 'specific_guideline') label = 'Specific Guideline';
            else if (docKey === 'general_faq') label = 'General FAQ';
            return (
              <span key={docKey} className="version-chip">
                {label} <strong>v{ver}</strong>
              </span>
            );
          })
        ) : (
          <span className="version-chip" style={{ opacity: 0.7 }}>
            {isWakingUp ? 'Loading document versions...' : 'Guideline versions unavailable'}
          </span>
        )}
      </div>

      <nav className="tabs-nav" style={{ marginTop: '0.9rem' }}>
        <button
          className={`tab-btn ${activeTab === 'ask' ? 'active' : ''}`}
          onClick={() => setActiveTab('ask')}
          type="button"
        >
          Ask Assistant
        </button>
        <button
          className={`tab-btn ${activeTab === 'check' ? 'active' : ''}`}
          onClick={() => setActiveTab('check')}
          type="button"
        >
          Check Invoice
          <span className="tab-badge-quota">No Quota</span>
        </button>
      </nav>
    </header>
  );
};
