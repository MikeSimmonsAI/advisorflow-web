import React from 'react';
import { StatusBar } from 'expo-status-bar';
import { ExperienceTabs } from '../../src/components/ExperienceTabs';

// The SCI / program product experience shares the app engine but never the
// navigation or cached data of Wholesale, Atlantis, or a general workspace.
export default function ProgramLayout() {
  return <><StatusBar style="dark" /><ExperienceTabs kind="program" /></>;
}
