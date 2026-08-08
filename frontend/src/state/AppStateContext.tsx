import { createContext, useContext, useReducer, type ReactElement, type ReactNode } from 'react';
import { createInitialState, type AppState } from './types';
import { reducer, type Action } from './reducer';

interface AppStateContextValue {
  state: AppState;
  dispatch: React.Dispatch<Action>;
}

const AppStateContext = createContext<AppStateContextValue | null>(null);

interface AppStateProviderProps {
  children: ReactNode;
}

export function AppStateProvider({ children }: AppStateProviderProps): ReactElement {
  const [state, dispatch] = useReducer(reducer, undefined, createInitialState);
  return (
    <AppStateContext.Provider value={{ state, dispatch }}>{children}</AppStateContext.Provider>
  );
}

export function useAppState(): AppStateContextValue {
  const ctx = useContext(AppStateContext);
  if (!ctx) throw new Error('useAppState must be used within AppStateProvider');
  return ctx;
}
