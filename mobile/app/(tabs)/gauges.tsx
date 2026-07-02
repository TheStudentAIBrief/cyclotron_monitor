import { useCallback, useEffect, useState } from 'react';
import {
  View, Text, TouchableOpacity, StyleSheet, TextInput, Switch,
  ScrollView, Image, ActivityIndicator, RefreshControl, Alert,
} from 'react-native';
import * as ImagePicker from 'expo-image-picker';
import {
  submitGaugePhoto, submitManualGauge, getGauges, deleteGauge, GaugeReading,
} from '../../services/api';
import { gaugeStatus, STATUS_COLORS, GaugeStatus } from '../../utils/gaugeStatus';
import { Colors } from '../../constants/Theme';

export default function GaugesScreen() {
  const [photoUri, setPhotoUri] = useState<string | null>(null);
  const [ocrText, setOcrText] = useState<string | null>(null);
  const [gaugeName, setGaugeName] = useState('');
  const [value, setValue] = useState('');
  const [unit, setUnit] = useState('');
  const [isAlert, setIsAlert] = useState(false);
  const [alertReason, setAlertReason] = useState('');
  const [capturing, setCapturing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState<{ text: string; ok: boolean } | null>(null);
  const [history, setHistory] = useState<GaugeReading[]>([]);
  const [refreshing, setRefreshing] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [deletingId, setDeletingId] = useState<number | null>(null);

  const loadHistory = useCallback(async () => {
    try {
      const res = await getGauges(1);
      setHistory(res.items);
      setLoadError(null);
    } catch (e: unknown) {
      setLoadError(e instanceof Error ? e.message : 'Failed to load readings');
    }
  }, []);

  useEffect(() => { loadHistory(); }, [loadHistory]);

  const onRefresh = useCallback(async () => {
    setRefreshing(true);
    await loadHistory();
    setRefreshing(false);
  }, [loadHistory]);

  async function capturePhoto() {
    const perm = await ImagePicker.requestCameraPermissionsAsync();
    if (!perm.granted) {
      setMessage({ text: 'Camera permission is required to log gauges.', ok: false });
      return;
    }
    setCapturing(true);
    setMessage(null);
    try {
      const result = await ImagePicker.launchCameraAsync({
        mediaTypes: ['images'],
        base64: true,
        quality: 0.8,
        allowsEditing: false,
      });
      if (result.canceled) return;
      if (!result.assets[0].base64) {
        setMessage({ text: 'Photo could not be encoded — please try again.', ok: false });
        return;
      }

      setPhotoUri(result.assets[0].uri);
      const ocr = await submitGaugePhoto(result.assets[0].base64, gaugeName) as Record<string, unknown>;
      if (ocr.value != null) {
        setValue(String(ocr.value));
        if (ocr.unit) setUnit(String(ocr.unit));
        if (ocr.is_alert) setIsAlert(true);
        setOcrText(String(ocr.raw_ocr_text ?? ''));
      } else {
        // No value read — don't present backend diagnostics as if they were a reading.
        setOcrText('');
        setMessage({
          text: ocr.ocr_ok === false
            ? 'AI reader unavailable — enter the value manually below.'
            : 'Could not read a value from the photo — enter it manually below.',
          ok: false,
        });
      }
    } catch (e: unknown) {
      setMessage({ text: e instanceof Error ? e.message : 'Photo capture failed', ok: false });
    } finally {
      setCapturing(false);
    }
  }

  async function handleSave() {
    if (!gaugeName.trim()) {
      setMessage({ text: 'Enter a gauge name before saving.', ok: false });
      return;
    }
    const numValue = parseFloat(value);
    if (isNaN(numValue)) {
      setMessage({ text: 'Enter a numeric value.', ok: false });
      return;
    }
    setSaving(true);
    setMessage(null);
    try {
      await submitManualGauge({
        gauge_name: gaugeName.trim(),
        value: numValue,
        unit: unit.trim(),
        is_alert: isAlert,
        alert_reason: alertReason.trim(),
      });
      setMessage({ text: 'Reading saved.', ok: true });
      // Reset form
      setPhotoUri(null);
      setOcrText(null);
      setValue('');
      setUnit('');
      setIsAlert(false);
      setAlertReason('');
      await loadHistory();
    } catch (e: unknown) {
      setMessage({ text: e instanceof Error ? e.message : 'Save failed', ok: false });
    } finally {
      setSaving(false);
    }
  }

  function handleDelete(id: number, gaugeName: string) {
    const name = gaugeName || 'Unnamed';
    Alert.alert(
      'Delete Reading',
      `Remove the reading for "${name}"?`,
      [
        { text: 'Cancel', style: 'cancel' },
        {
          text: 'Delete',
          style: 'destructive',
          onPress: () =>
            Alert.alert(
              'Confirm Deletion',
              'This reading will be permanently deleted and cannot be recovered.',
              [
                { text: 'Cancel', style: 'cancel' },
                {
                  text: 'Yes, Delete',
                  style: 'destructive',
                  onPress: async () => {
                    setDeletingId(id);
                    try {
                      await deleteGauge(id);
                      await loadHistory();
                    } catch (e: unknown) {
                      setMessage({ text: e instanceof Error ? e.message : 'Delete failed', ok: false });
                    } finally {
                      setDeletingId(null);
                    }
                  },
                },
              ],
            ),
        },
      ],
    );
  }

  return (
    <ScrollView
      style={styles.container}
      keyboardShouldPersistTaps="handled"
      refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={Colors.primary} />}
    >
      <View style={styles.section}>

        {/* Camera capture */}
        <TouchableOpacity
          style={styles.captureBtn}
          onPress={capturePhoto}
          disabled={capturing || saving}
          activeOpacity={0.8}
        >
          {capturing
            ? <ActivityIndicator color={Colors.white} />
            : <Text style={styles.captureBtnText}>📷  Photograph Gauge</Text>
          }
        </TouchableOpacity>

        {/* Photo preview */}
        {photoUri && (
          <Image source={{ uri: photoUri }} style={styles.preview} resizeMode="contain" />
        )}

        {/* OCR result */}
        {ocrText != null && (
          <View style={styles.ocrBox}>
            <Text style={styles.ocrLabel}>OCR Result</Text>
            <Text style={styles.ocrText}>
              {ocrText || 'No text detected — enter reading manually below'}
            </Text>
          </View>
        )}

        {/* Manual entry form */}
        <Text style={styles.fieldLabel}>Gauge Name *</Text>
        <TextInput
          style={styles.input}
          placeholder="e.g. Vacuum Pump Pressure"
          placeholderTextColor="#444"
          value={gaugeName}
          onChangeText={setGaugeName}
        />

        <View style={styles.rowFields}>
          <View style={styles.valueField}>
            <Text style={styles.fieldLabel}>Reading *</Text>
            <TextInput
              style={styles.input}
              placeholder="0.00"
              placeholderTextColor="#444"
              keyboardType="decimal-pad"
              value={value}
              onChangeText={setValue}
            />
          </View>
          <View style={styles.unitField}>
            <Text style={styles.fieldLabel}>Unit</Text>
            <TextInput
              style={styles.input}
              placeholder="mbar"
              placeholderTextColor="#444"
              value={unit}
              onChangeText={setUnit}
            />
          </View>
        </View>

        <View style={styles.alertRow}>
          <Text style={styles.fieldLabel}>Mark as Alert</Text>
          <Switch
            value={isAlert}
            onValueChange={setIsAlert}
            trackColor={{ false: Colors.borderDark, true: Colors.alertRedBg }}
            thumbColor={isAlert ? Colors.alertRed : '#555'}
          />
        </View>

        {isAlert && (
          <>
            <Text style={styles.fieldLabel}>Alert Reason</Text>
            <TextInput
              style={styles.input}
              placeholder="Describe the issue"
              placeholderTextColor="#444"
              value={alertReason}
              onChangeText={setAlertReason}
            />
          </>
        )}

        {message && (
          <Text style={[styles.msg, message.ok ? styles.msgOk : styles.msgErr]}>
            {message.text}
          </Text>
        )}

        <TouchableOpacity
          style={[styles.saveBtn, (saving || capturing) && { opacity: 0.55 }]}
          onPress={handleSave}
          disabled={saving || capturing}
          activeOpacity={0.8}
        >
          {saving
            ? <ActivityIndicator color={Colors.white} />
            : <Text style={styles.saveBtnText}>Save Reading</Text>
          }
        </TouchableOpacity>
      </View>

      {/* History */}
      <View style={styles.history}>
        <Text style={styles.historyTitle}>Recent Readings</Text>
        {history.length === 0
          ? <Text style={loadError ? styles.loadErr : styles.empty}>
              {loadError ?? 'No readings yet.'}
            </Text>
          : history.map(item => {
            const st: GaugeStatus = (item.status as GaugeStatus) ||
              gaugeStatus(item.value, item.alert_lo ?? null, item.alert_hi ?? null,
                          item.action_lo ?? null, item.action_hi ?? null);
            const col = STATUS_COLORS[st];
            return (
              <View key={item.id} style={[styles.histItem, { borderLeftColor: col.border, borderLeftWidth: 3 }]}>
                <View style={styles.histHeader}>
                  <Text style={styles.histGauge}>{item.gauge_name || 'Unnamed'}</Text>
                  <View style={styles.histHeaderRight}>
                    <View style={[styles.statusPill, { backgroundColor: col.bg }]}>
                      <Text style={[styles.statusPillText, { color: col.text }]}>{st}</Text>
                    </View>
                    {deletingId === item.id
                      ? <ActivityIndicator size="small" color={Colors.alertRed} style={styles.deleteSpinner} />
                      : (
                        <TouchableOpacity
                          onPress={() => handleDelete(item.id, item.gauge_name)}
                          disabled={deletingId !== null}
                          style={[styles.deleteBtn, deletingId !== null && { opacity: 0.35 }]}
                          hitSlop={{ top: 10, bottom: 10, left: 10, right: 10 }}
                          activeOpacity={0.6}
                        >
                          <Text style={styles.deleteBtnText}>✕</Text>
                        </TouchableOpacity>
                      )
                    }
                  </View>
                </View>
                {item.location ? (
                  <Text style={styles.histLocation}>{item.location}</Text>
                ) : null}
                <Text style={styles.histValue}>
                  {item.value != null
                    ? `${item.value}${item.unit ? ' ' + item.unit : ''}`
                    : 'No reading extracted'}
                </Text>
                <Text style={styles.histTs}>
                  {item.timestamp.slice(0, 10)} {item.timestamp.slice(11, 16)} UTC
                  {item.verified_by ? ` · verified by ${item.verified_by}` : ''}
                </Text>
                {item.alert_reason && item.alert_reason !== 'NORMAL' ? (
                  <Text style={styles.histReason}>{item.alert_reason}</Text>
                ) : null}
              </View>
            );
          })
        }
      </View>
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: Colors.ink },
  section: { padding: 16 },

  captureBtn: {
    backgroundColor: Colors.primary,
    borderRadius: 10,
    paddingVertical: 18,
    alignItems: 'center',
    marginBottom: 14,
  },
  captureBtnText: { color: Colors.white, fontSize: 16, fontWeight: '600' },

  preview: {
    width: '100%',
    height: 200,
    backgroundColor: Colors.surfaceDarkAlt,
    borderRadius: 8,
    marginBottom: 12,
  },

  ocrBox: {
    backgroundColor: Colors.surfaceDarkBlue,
    borderRadius: 8,
    padding: 12,
    marginBottom: 14,
    borderWidth: 1,
    borderColor: Colors.borderBlue,
  },
  ocrLabel: { color: Colors.primary, fontSize: 10, fontWeight: '600', marginBottom: 5, textTransform: 'uppercase' },
  ocrText: { color: '#aaa', fontSize: 13 },

  fieldLabel: { color: '#888', fontSize: 12, marginBottom: 5, marginTop: 12 },
  input: {
    backgroundColor: Colors.surfaceDarkAlt,
    color: Colors.white,
    borderRadius: 8,
    borderWidth: 1,
    borderColor: Colors.borderDark,
    paddingHorizontal: 12,
    paddingVertical: 11,
    fontSize: 14,
  },
  rowFields: { flexDirection: 'row', gap: 8 },
  valueField: { flex: 2 },
  unitField: { flex: 1 },
  alertRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginTop: 12,
    marginBottom: 4,
  },

  msg: { fontSize: 13, textAlign: 'center', marginTop: 10, padding: 8, borderRadius: 6, overflow: 'hidden' },
  msgOk: { backgroundColor: Colors.alertGreenBg, color: Colors.alertGreen },
  msgErr: { backgroundColor: Colors.alertRedBg, color: Colors.alertRed },

  saveBtn: {
    backgroundColor: Colors.alertGreen,
    borderRadius: 8,
    paddingVertical: 14,
    alignItems: 'center',
    marginTop: 14,
  },
  saveBtnText: { color: Colors.white, fontSize: 15, fontWeight: '600' },

  history: { padding: 16, paddingTop: 4 },
  historyTitle: {
    color: '#555',
    fontSize: 11,
    fontWeight: '600',
    textTransform: 'uppercase',
    letterSpacing: 1,
    marginBottom: 10,
  },
  histItem: {
    backgroundColor: Colors.surfaceDark,
    borderRadius: 8,
    padding: 12,
    marginBottom: 8,
    borderWidth: 1,
    borderColor: Colors.borderDark,
  },
  histHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: 4,
  },
  histGauge: { color: '#ccc', fontSize: 14, fontWeight: '600', flex: 1 },
  statusPill: { borderRadius: 4, paddingHorizontal: 6, paddingVertical: 2 },
  statusPillText: { fontSize: 10, fontWeight: '700' },
  histHeaderRight: { flexDirection: 'row', alignItems: 'center', gap: 10 },
  deleteBtn: { paddingHorizontal: 4, paddingVertical: 2 },
  deleteBtnText: { color: Colors.alertRed, fontSize: 16, fontWeight: '700', lineHeight: 18 },
  deleteSpinner: { width: 20 },
  histLocation: { color: Colors.textMuted, fontSize: 12, marginBottom: 3 },
  histValue: { color: Colors.white, fontSize: 18, fontWeight: '700', marginBottom: 3 },
  histTs: { color: '#555', fontSize: 11 },
  histReason: { color: Colors.alertOrange, fontSize: 12, marginTop: 4 },
  empty: { color: '#555', textAlign: 'center', marginTop: 20 },
  loadErr: { color: Colors.alertRed, textAlign: 'center', marginTop: 20, fontSize: 12, paddingHorizontal: 8 },
});
