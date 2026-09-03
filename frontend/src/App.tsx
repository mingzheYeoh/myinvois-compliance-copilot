import React, { useState, useEffect, useCallback, useRef } from 'react';
import { HealthResponse } from './types';
import { Header } from './components/Header';
import { AskChat } from './components/AskChat';
import { CheckInvoice } from './components/CheckInvoice';
import { Footer } from './components/Footer';

export const App: React.FC = () => {
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [isWakingUp, setIsWakingUp] = useState<boolean>(true);
  const [isHealthy, setIsHealthy] = useState<boolean>(false);
  const [activeTab, setActiveTab] = useState<'ask' | 'check'>('ask');

  const pollIntervalRef = useRef<number | null>(null);

  const fetchHealth = useCallback(async () => {
    try {
      const res = await fetch('/health');
      if (!res.ok) {
        setIsWakingUp(true);
        setIsHealthy(false);
        return;
      }
      const data: HealthResponse = await res.json();
      setHealth(data);

      // Only consider healthy when status is strictly "ok" (not degraded or down)
      if (data.status === 'ok') {
        setIsWakingUp(false);
        setIsHealthy(true);
      } else {
        // Degraded state (e.g. DB fail or empty guideline versions)
        setIsWakingUp(true);
        setIsHealthy(false);
      }
    } catch {
      // Network failure / cold starting
      setIsWakingUp(true);
      setIsHealthy(false);
    }
  }, []);

  useEffect(() => {
    // Initial fetch
    fetchHealth();

    // Poll rapidly during cold start (~2.5s), relax to 20s once healthy
    const intervalTime = isHealthy ? 20000 : 2500;
    pollIntervalRef.current = window.setInterval(fetchHealth, intervalTime);

    return () => {
      if (pollIntervalRef.current) {
        clearInterval(pollIntervalRef.current);
      }
    };
  }, [fetchHealth, isHealthy]);

  return (
    <div className="app-container">
      <Header
        health={health}
        isWakingUp={isWakingUp}
        activeTab={activeTab}
        setActiveTab={setActiveTab}
      />

      <main style={{ flex: 1, display: 'flex', flexDirection: 'column' }}>
        {activeTab === 'ask' ? (
          <AskChat
            isHealthy={isHealthy}
            onSwitchToCheckInvoice={() => setActiveTab('check')}
          />
        ) : (
          <CheckInvoice />
        )}
      </main>

      <Footer />
    </div>
  );
};
