import { mount } from 'svelte'
import './app.css'
import './settings.css'
import Settings from './Settings.svelte'

mount(Settings, { target: document.getElementById('app')! })
