import { theme } from 'antd'

const lightTheme = {
  token: {
    colorPrimary: '#4f46e5',
    colorPrimaryHover: '#4338ca',
    colorPrimaryActive: '#3730a3',
    colorBgBase: '#f8fbff',
    colorTextBase: '#0f172a',
    colorBgContainer: '#ffffff',
    colorBgElevated: '#ffffff',
    colorBorder: '#dbe5ef',
    colorBorderSecondary: '#e8eef6',
    borderRadius: 14,
    colorText: '#0f172a',
    colorTextSecondary: '#475569',
    colorTextTertiary: '#94a3b8',
    colorBgLayout: '#eef3f8',
    colorFillAlter: '#f8fafc',
    colorFillQuaternary: '#f8fafc',
    boxShadowTertiary: '0 18px 45px rgba(15, 23, 42, 0.08)',
  },
  components: {
    Layout: {
      bodyBg: '#eef3f8',
      headerBg: '#ffffff',
      siderBg: '#ffffff',
      triggerBg: '#ffffff',
      triggerColor: '#0f172a',
    },
    Card: {
      colorBgContainer: '#ffffff',
      colorBorderSecondary: '#dbe5ef',
      borderRadiusLG: 20,
      boxShadowTertiary: '0 18px 45px rgba(15, 23, 42, 0.08)',
      paddingLG: 24,
    },
    Table: {
      headerBg: '#f8fafc',
      headerColor: '#334155',
      borderColor: '#dbe5ef',
      rowHoverBg: '#f8fafc',
      colorBgContainer: '#ffffff',
    },
    Menu: {
      itemBg: 'transparent',
      itemColor: '#334155',
      itemHoverBg: '#f8fafc',
      itemHoverColor: '#1e293b',
      itemSelectedBg: '#eef2ff',
      itemSelectedColor: '#4338ca',
      itemBorderRadius: 12,
    },
    Button: {
      borderRadius: 12,
      controlHeight: 36,
    },
    Tag: {
      borderRadiusSM: 999,
    },
    Modal: {
      contentBg: '#ffffff',
      headerBg: '#ffffff',
      borderRadiusLG: 20,
    },
    Descriptions: {
      labelBg: '#f8fafc',
    },
    Form: {
      labelColor: '#334155',
    },
  },
  algorithm: theme.defaultAlgorithm,
}

export { lightTheme }
