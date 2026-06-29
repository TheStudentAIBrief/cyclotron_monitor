import { createContext, useContext } from 'react';

interface AuthContextValue {
  setAuthed: (authed: boolean) => void;
}

export const AuthContext = createContext<AuthContextValue>({ setAuthed: () => {} });
export const useAuth = () => useContext(AuthContext);
